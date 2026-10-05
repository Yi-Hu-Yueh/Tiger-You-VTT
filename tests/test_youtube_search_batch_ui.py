from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def page() -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def between(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


def test_search_section_is_first_and_has_independent_controls() -> None:
    html = page()
    search = between(html, '<fieldset id="youtubeSearchSection">', "</fieldset>")

    assert html.index('id="youtubeSearchSection"') < html.index('id="sourceSection"')
    assert 'id="youtubeSearchStartButton"' in search
    assert 'id="youtubeSearchStopButton"' in search
    assert 'id="startButton"' not in search
    assert 'id="stopButton"' not in search


def test_renderer_creates_video_id_bound_checkboxes_and_title_links() -> None:
    html = page()
    renderer = between(
        html,
        "function renderSearchResults(results)",
        'youtubeSearchButton.addEventListener("click"',
    )

    assert 'checkbox.type = "checkbox"' in renderer
    assert 'checkbox.value = result.video_id' in renderer
    assert 'checkbox.dataset.videoId = result.video_id' in renderer
    assert 'checkbox.dataset.url = result.url' in renderer
    assert "title.href = result.url" in renderer
    assert 'title.target = "_blank"' in renderer


def test_first_three_are_default_selected_for_every_new_result_list() -> None:
    html = page()
    renderer = between(
        html,
        "function renderSearchResults(results)",
        'youtubeSearchButton.addEventListener("click"',
    )

    assert "results.forEach((result, index)" in renderer
    assert "checkbox.checked = index < 3" in renderer
    assert "setSearchVideoSelected(result, checkbox.checked)" in renderer


def test_one_and_two_result_lists_select_every_available_result() -> None:
    html = page()
    assert "checkbox.checked = index < 3" in html
    assert "results.forEach((result, index)" in html


def test_manual_checkbox_changes_update_selected_video_state() -> None:
    html = page()

    assert 'checkbox.addEventListener("change"' in html
    assert "setSearchVideoSelected(result, checkbox.checked)" in html
    assert "selectedSearchVideos.delete(result.video_id)" in html
    assert "selectedSearchVideos.set(result.video_id" in html


def test_selected_state_retains_video_id_title_and_api_url() -> None:
    html = page()
    selection = between(
        html,
        "function setSearchVideoSelected(result, checked)",
        "function resetSearchSelection()",
    )

    assert "video_id: result.video_id" in selection
    assert "title: result.title" in selection
    assert "url: result.url" in selection


def test_zero_selected_disables_start_and_new_search_resets_selection() -> None:
    html = page()
    controls = between(
        html,
        "function updateSearchBatchControls()",
        "function setSearchRowStatus",
    )
    search_handler = between(
        html,
        'youtubeSearchButton.addEventListener("click"',
        "function getCheckedSearchVideos()",
    )

    assert "selectedSearchVideos.size === 0" in controls
    assert "resetSearchSelection();" in search_handler
    assert "selectedSearchVideos.clear()" in html


def test_batch_reads_only_checked_results_in_display_order() -> None:
    html = page()
    checked = between(
        html,
        "function getCheckedSearchVideos()",
        "function searchJobStatusLabel",
    )
    batch = between(html, "async function runSearchBatch()", "youtubeSearchStartButton")

    assert 'querySelectorAll(".youtube-search-select:checked")' in checked
    assert "selectedSearchVideos.get(checkbox.dataset.videoId)" in checked
    assert "const queue = getCheckedSearchVideos().filter(Boolean)" in batch


def test_batch_reuses_youtube_endpoint_and_fixed_default_range() -> None:
    html = page()
    process = between(
        html,
        "async function processSearchVideo(video)",
        "async function runSearchBatch()",
    )

    assert 'fetch("/api/jobs/youtube"' in process
    assert "url: video.url" in process
    assert 'const SEARCH_BATCH_START_TIME = "0:0"' in html
    assert 'const SEARCH_BATCH_END_TIME = "0:10"' in html
    assert "start_time: SEARCH_BATCH_START_TIME" in process
    assert "end_time: SEARCH_BATCH_END_TIME" in process
    assert "end_time_is_default: true" in process
    assert "enable_diarization: youtubeSearchDiarization.checked" in process


def test_batch_is_strictly_sequential_and_failure_does_not_abort_queue() -> None:
    html = page()
    batch = between(html, "async function runSearchBatch()", "youtubeSearchStartButton")
    process = between(
        html,
        "async function processSearchVideo(video)",
        "async function runSearchBatch()",
    )

    assert "for (const video of queue)" in batch
    assert "await processSearchVideo(video)" in batch
    assert "await waitForSearchJobTerminal(activeSearchJobId)" in process
    assert 'setSearchRowStatus(video.video_id, "失敗")' in process
    assert 'return "failed"' in process
    assert "outcomes.set(video.video_id, status)" in batch


def test_stop_targets_only_active_search_job_and_prevents_next_job() -> None:
    html = page()
    stop_function = between(
        html,
        "async function stopActiveSearchJob()",
        "async function processSearchVideo(video)",
    )
    batch = between(html, "async function runSearchBatch()", "youtubeSearchStartButton")
    stop_handler = between(
        html,
        'youtubeSearchStopButton.addEventListener("click"',
        "function formatElapsedTime",
    )

    assert "if (!activeSearchJobId) return null" in stop_function
    assert "`/api/jobs/${activeSearchJobId}/stop`" in stop_function
    assert "activeJobId" not in stop_function
    assert "if (searchBatchStopRequested) break" in batch
    assert "searchBatchStopRequested = true" in stop_handler


def test_search_and_selection_controls_lock_during_batch_then_restore() -> None:
    html = page()
    batch = between(html, "async function runSearchBatch()", "youtubeSearchStartButton")
    controls = between(
        html,
        "function updateSearchBatchControls()",
        "function setSearchRowStatus",
    )

    assert "setSearchControlsLocked(true)" in batch
    assert "setSearchControlsLocked(false)" in batch
    assert "youtubeSearchStartButton.disabled = searchBatchRunning" in controls
    assert "youtubeSearchStopButton.disabled = !searchBatchRunning" in controls
    assert "checkbox.disabled = searchBatchRunning" in controls


def test_each_selected_row_uses_only_the_allowed_status_labels() -> None:
    html = page()

    for label in ("等待中", "處理中", "完成", "已停止", "失敗"):
        assert f'"{label}"' in html
    assert 'className = "search-job-status"' in html


def test_search_does_not_automatically_start_transcription() -> None:
    html = page()
    search_handler = between(
        html,
        'youtubeSearchButton.addEventListener("click"',
        "function getCheckedSearchVideos()",
    )

    assert "/api/youtube/search" in search_handler
    assert "/api/jobs/youtube" not in search_handler
    assert "runSearchBatch" not in search_handler

