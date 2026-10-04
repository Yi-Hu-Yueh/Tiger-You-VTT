from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def page() -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def between(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


def test_exactly_two_accessible_primary_tabs_exist() -> None:
    html = page()
    tab_list = between(html, '<div class="primary-tabs"', "</div>")

    assert tab_list.count('role="tab"') == 2
    assert 'id="youtubeSearchTab"' in tab_list
    assert '>YouTube 搜尋</button>' in tab_list
    assert 'id="sourceTab"' in tab_list
    assert '>來源</button>' in tab_list


def test_search_tab_is_default_and_source_panel_is_initially_hidden() -> None:
    html = page()

    assert 'id="youtubeSearchTab" type="button" role="tab" aria-selected="true"' in html
    assert 'id="sourceTab" type="button" role="tab" aria-selected="false"' in html
    assert 'id="youtubeSearchTabPanel" role="tabpanel"' in html
    assert 'id="sourceTabPanel" role="tabpanel" aria-labelledby="sourceTab" hidden' in html


def test_tab_switching_actually_toggles_panel_visibility() -> None:
    html = page()
    activate = between(html, "function activatePrimaryTab(tab)", "primaryTabs.forEach")

    assert "youtubeSearchTabPanel.hidden = !searchActive" in activate
    assert "sourceTabPanel.hidden = searchActive" in activate
    assert 'tab.addEventListener("click", () => activatePrimaryTab(tab))' in html
    assert "activatePrimaryTab(youtubeSearchTab)" in html


def test_tab_switching_preserves_search_and_source_dom_state() -> None:
    html = page()
    activate = between(html, "function activatePrimaryTab(tab)", "primaryTabs.forEach")

    assert ".replaceChildren" not in activate
    assert ".value" not in activate
    assert "selectedSearchVideos" not in activate
    assert "youtubeSearchTabPanel.hidden" in activate
    assert "sourceTabPanel.hidden" in activate


def test_search_and_source_workflows_are_in_their_respective_panels() -> None:
    html = page()
    search_panel = between(
        html,
        '<section id="youtubeSearchTabPanel"',
        '<section id="sourceTabPanel"',
    )
    source_panel = between(html, '<section id="sourceTabPanel"', "</main>")

    assert 'id="youtubeSearchQuery"' in search_panel
    assert 'id="youtubeSearchResults"' in search_panel
    assert 'id="youtubeSearchStartButton"' in search_panel
    assert 'id="youtubeTranscriptPanels"' in search_panel
    assert 'id="sourceSection"' in source_panel
    assert 'id="rangeControls"' in source_panel
    assert 'id="startButton"' in source_panel
    assert 'id="transcriptText"' in source_panel
    assert 'data-format="txt"' in source_panel
    assert 'data-format="vtt"' in source_panel
    assert 'data-format="srt"' in source_panel


def test_transcript_panel_is_keyed_by_video_id_and_contains_required_fields() -> None:
    html = page()
    create = between(
        html,
        "function createSearchTranscriptPanel(result)",
        "function syncSearchTranscriptPanels()",
    )

    assert "panel.dataset.videoId = result.video_id" in create
    assert "`影片：${result.title}`" in create
    assert 'status.textContent = "等待中"' in create
    assert 'error.className = "search-transcript-error"' in create
    assert "error.hidden = true" in create
    assert 'text.className = "search-transcript-text"' in create
    assert "text.readOnly = true" in create
    assert 'text.placeholder = "尚未取得字幕"' in create


def test_checkbox_selection_immediately_synchronizes_panels() -> None:
    html = page()
    selection = between(
        html,
        "function setSearchVideoSelected(result, checked)",
        "function resetSearchSelection()",
    )

    assert "selectedSearchVideos.set(result.video_id" in selection
    assert "selectedSearchVideos.delete(result.video_id)" in selection
    assert "syncSearchTranscriptPanels()" in selection
    assert 'checked ? "等待中" : ""' in selection


def test_one_panel_per_selected_video_without_duplicates() -> None:
    html = page()
    sync = between(
        html,
        "function syncSearchTranscriptPanels()",
        "function setSearchTranscriptText",
    )

    assert "searchTranscriptPanels.get(result.video_id)" in sync
    assert "if (!transcriptPanel)" in sync
    assert "searchTranscriptPanels.set(result.video_id, transcriptPanel)" in sync
    assert "selectedIds.has(result.video_id)" in sync


def test_panels_follow_search_result_order_not_checkbox_click_order() -> None:
    html = page()
    sync = between(
        html,
        "function syncSearchTranscriptPanels()",
        "function setSearchTranscriptText",
    )

    assert "searchResultRows.forEach(({ result })" in sync
    assert "youtubeTranscriptPanels.append(transcriptPanel.panel)" in sync
    assert "selectedSearchVideos.forEach" not in sync


def test_default_first_three_results_immediately_create_three_panels() -> None:
    html = page()
    renderer = between(
        html,
        "function renderSearchResults(results)",
        'youtubeSearchButton.addEventListener("click"',
    )

    assert "checkbox.checked = index < 3" in renderer
    assert "setSearchVideoSelected(result, checkbox.checked)" in renderer


def test_new_search_removes_old_panels_before_rendering_defaults() -> None:
    html = page()
    reset = between(
        html,
        "function resetSearchSelection()",
        "function renderSearchResults(results)",
    )
    search = between(
        html,
        'youtubeSearchButton.addEventListener("click"',
        "function getCheckedSearchVideos()",
    )

    assert "searchTranscriptPanels.clear()" in reset
    assert "youtubeTranscriptPanels.replaceChildren()" in reset
    assert "resetSearchSelection();" in search
    assert "renderSearchResults(payload.results)" in search


def test_polling_updates_only_the_active_video_panel_with_partial_text() -> None:
    html = page()
    poll = between(
        html,
        "async function waitForSearchJobTerminal(jobId)",
        "async function stopActiveSearchJob()",
    )

    assert "setSearchTranscriptText(activeSearchVideoId, job.txt)" in poll
    assert "setSearchPanelError(activeSearchVideoId, job.error)" in poll
    assert "setSearchRowStatus(activeSearchVideoId" in poll
    assert "if (terminalStates.has(job.status)) return job" in poll


def test_completed_and_failed_panels_are_retained() -> None:
    html = page()
    process = between(
        html,
        "async function processSearchVideo(video)",
        "async function runSearchBatch()",
    )

    assert "setSearchTranscriptText(video.video_id, terminalJob.txt)" in process
    assert 'setSearchRowStatus(video.video_id, "失敗")' in process
    assert "searchTranscriptPanels.delete" not in process
    assert "youtubeTranscriptPanels.replaceChildren" not in process


def test_failed_job_exposes_only_its_safe_code_and_message_in_its_panel() -> None:
    html = page()
    error_update = between(
        html,
        "function setSearchPanelError(videoId, error)",
        "function setSearchRowStatus",
    )

    assert "searchTranscriptPanels.get(videoId)" in error_update
    assert "if (!error?.code || !error?.message)" in error_update
    assert "`錯誤：${error.code} - ${error.message}`" in error_update
    assert ".innerHTML" not in error_update


def test_batch_summary_reports_terminal_outcome_counts() -> None:
    html = page()
    summary = between(
        html,
        "function formatSearchBatchSummary(queue, outcomes, stoppedByUser)",
        "async function runSearchBatch()",
    )

    assert 'status === "completed"' in summary
    assert 'status === "failed"' in summary
    assert 'status === "stopped"' in summary
    assert "queue.length - outcomes.size" in summary
    assert "${completed} 成功" in summary
    assert "${failed} 失敗" in summary
    assert "${notStarted} 未開始" in summary


def test_stop_updates_only_active_panel_and_leaves_waiting_panels() -> None:
    html = page()
    stop = between(
        html,
        "async function stopActiveSearchJob()",
        "async function processSearchVideo(video)",
    )
    batch = between(html, "async function runSearchBatch()", "youtubeSearchStartButton")

    assert "activeSearchJobId" in stop
    assert "setSearchRowStatus(activeSearchVideoId" in stop
    assert "setSearchTranscriptText(activeSearchVideoId, job.txt)" in stop
    assert "if (searchBatchStopRequested) break" in batch


def test_source_transcript_remains_separate_from_search_panels() -> None:
    html = page()

    assert html.count('id="transcriptText"') == 1
    assert html.count('id="youtubeTranscriptPanels"') == 1
    assert 'className = "search-transcript-text"' in html
    assert "transcriptText.value = job.txt" in html
    assert "setSearchTranscriptText(activeSearchVideoId, job.txt)" in html

