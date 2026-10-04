from __future__ import annotations

import html as html_module
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _chrome_path() -> Path:
    candidates = [
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise AssertionError("Chrome/Chromium is required for deterministic UI browser tests")


def _browser_fixture_script() -> str:
    return r"""
<pre id="browser-test-result">pending</pre>
<script>
(async () => {
  const sleep = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds));
  const waitFor = async (condition, timeout = 5000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (condition()) return;
      await sleep(10);
    }
    throw new Error('browser test timeout');
  };
  const result = {};

  result.initialTabs = {
    count: document.querySelectorAll('.primary-tabs [role=tab]').length,
    searchSelected: document.querySelector('#youtubeSearchTab').getAttribute('aria-selected'),
    searchVisible: !document.querySelector('#youtubeSearchTabPanel').hidden,
    sourceHidden: document.querySelector('#sourceTabPanel').hidden
  };
  document.querySelector('#youtubeSearchQuery').value = 'Tiger';
  document.querySelector('#sourceTab').click();
  document.querySelector('#youtubeUrl').value = 'source-preserved';
  result.sourceActive = {
    searchHidden: document.querySelector('#youtubeSearchTabPanel').hidden,
    sourceVisible: !document.querySelector('#sourceTabPanel').hidden
  };
  document.querySelector('#youtubeSearchTab').click();
  result.preserved = {
    query: document.querySelector('#youtubeSearchQuery').value,
    sourceUrl: document.querySelector('#youtubeUrl').value
  };

  const videos = ['a', 'b', 'c', 'd'].map(id => ({
    video_id: id,
    title: `Video ${id.toUpperCase()}`,
    url: `https://www.youtube.com/watch?v=${id}`,
    channel: null,
    duration_text: null,
    view_count: null,
    upload_date: null
  }));
  renderSearchResults(videos);
  result.defaultPanels = {
    checked: Array.from(document.querySelectorAll('.youtube-search-select')).map(x => x.checked),
    ids: Array.from(document.querySelectorAll('.search-transcript-panel')).map(x => x.dataset.videoId)
  };
  const boxes = Array.from(document.querySelectorAll('.youtube-search-select'));
  boxes[1].checked = false;
  boxes[1].dispatchEvent(new Event('change'));
  result.afterUncheck = Array.from(document.querySelectorAll('.search-transcript-panel')).map(x => x.dataset.videoId);
  boxes[1].checked = true;
  boxes[1].dispatchEvent(new Event('change'));
  boxes[3].checked = true;
  boxes[3].dispatchEvent(new Event('change'));
  result.afterCheck = Array.from(document.querySelectorAll('.search-transcript-panel')).map(x => x.dataset.videoId);

  const events = [];
  const polls = {};
  const snapshots = {};
  const requestBodies = [];
  window.fetch = async (input, options = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    if (url === '/api/jobs/youtube') {
      const body = JSON.parse(options.body);
      requestBodies.push(body);
      const id = new URL(body.url).searchParams.get('v');
      events.push(`start:${id}`);
      return {ok:true, json:async () => ({job_id:`job-${id}`, status:'queued'})};
    }
    const match = url.match(/^\/api\/jobs\/job-([abcd])$/);
    if (match) {
      const id = match[1];
      if (id === 'c') {
        events.push('poll:c:failed');
        return {ok:true, json:async () => ({
          status:'failed',
          txt:'',
          error:{
            code:'whisper_transcription_failed',
            message:'Local faster-whisper transcription failed.'
          }
        })};
      }
      polls[id] = (polls[id] || 0) + 1;
      if (polls[id] === 2) {
        snapshots[`${id}Partial`] = Array.from(document.querySelectorAll('.search-transcript-panel')).map(panel => ({
          id: panel.dataset.videoId,
          status: panel.querySelector('.search-transcript-status').textContent,
          text: panel.querySelector('textarea').value
        }));
      }
      const running = polls[id] === 1;
      const status = running ? 'running' : 'completed';
      const txt = running ? `${id.toUpperCase()} partial` : `${id.toUpperCase()} final`;
      events.push(`poll:${id}:${status}`);
      return {ok:true, json:async () => ({status, txt})};
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  await runSearchBatch();
  result.sequence = {
    events,
    requestBodies,
    summary: document.querySelector('#youtubeSearchBatchStatus').textContent,
    snapshots,
    panels: Array.from(document.querySelectorAll('.search-transcript-panel')).map(panel => ({
      id: panel.dataset.videoId,
      status: panel.querySelector('.search-transcript-status').textContent,
      text: panel.querySelector('textarea').value,
      error: panel.querySelector('.search-transcript-error').textContent,
      errorHidden: panel.querySelector('.search-transcript-error').hidden
    }))
  };

  resetSearchSelection();
  renderSearchResults(videos.slice(0, 3));
  const allFailedEvents = [];
  window.fetch = async (input, options = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    if (url === '/api/jobs/youtube') {
      const id = new URL(JSON.parse(options.body).url).searchParams.get('v');
      allFailedEvents.push(`start:${id}`);
      return {ok:true, json:async () => ({job_id:`fail-${id}`, status:'queued'})};
    }
    const match = url.match(/^\/api\/jobs\/fail-([abc])$/);
    if (match) {
      const id = match[1];
      allFailedEvents.push(`poll:${id}:failed`);
      return {ok:true, json:async () => ({
        status:'failed', txt:'',
        error:{code:'whisper_transcription_failed', message:'Local faster-whisper transcription failed.'}
      })};
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  await runSearchBatch();
  result.allFailed = {
    events: allFailedEvents,
    summary: document.querySelector('#youtubeSearchBatchStatus').textContent,
    errors: Array.from(document.querySelectorAll('.search-transcript-error')).map(error => ({
      text: error.textContent,
      hidden: error.hidden
    }))
  };

  resetSearchSelection();
  renderSearchResults(videos.slice(0, 3));
  const stopEvents = [];
  let stopped = false;
  window.fetch = async (input, options = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    if (url === '/api/jobs/youtube') {
      const id = new URL(JSON.parse(options.body).url).searchParams.get('v');
      stopEvents.push(`start:${id}`);
      return {ok:true, json:async () => ({job_id:`job-${id}`, status:'queued'})};
    }
    if (url.endsWith('/stop')) {
      stopEvents.push(`stop:${url}`);
      stopped = true;
      return {ok:true, json:async () => ({status:'stopped', txt:'A retained partial'})};
    }
    if (url === '/api/jobs/job-a') {
      const status = stopped ? 'stopped' : 'running';
      const txt = stopped ? 'A retained partial' : 'A live partial';
      stopEvents.push(`poll:a:${status}`);
      return {ok:true, json:async () => ({status, txt})};
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  document.querySelector('#youtubeSearchStartButton').click();
  await waitFor(() => document.querySelector('.search-transcript-text').value === 'A live partial');
  document.querySelector('#youtubeSearchStopButton').click();
  await waitFor(() => !searchBatchRunning);
  result.stop = {
    events: stopEvents,
    summary: document.querySelector('#youtubeSearchBatchStatus').textContent,
    panels: Array.from(document.querySelectorAll('.search-transcript-panel')).map(panel => ({
      id: panel.dataset.videoId,
      status: panel.querySelector('.search-transcript-status').textContent,
      text: panel.querySelector('textarea').value
    }))
  };

  document.querySelector('#browser-test-result').textContent = JSON.stringify(result);
})().catch(error => {
  document.querySelector('#browser-test-result').textContent = JSON.stringify({error:String(error), stack:error.stack});
});
</script>
"""


def test_tabs_panels_partial_text_sequence_failure_and_stop_in_browser(tmp_path: Path) -> None:
    response = client.get("/")
    assert response.status_code == 200
    browser_html = response.text.replace(
        "</body>", f"{_browser_fixture_script()}</body>"
    )
    page_path = tmp_path / "tabbed-transcript-test.html"
    page_path.write_text(browser_html, encoding="utf-8")
    profile_path = tmp_path / "chrome-profile"

    completed = subprocess.run(
        [
            str(_chrome_path()),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-extensions",
            "--no-first-run",
            f"--user-data-dir={profile_path}",
            "--virtual-time-budget=7000",
            "--dump-dom",
            page_path.as_uri(),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    match = re.search(
        r'<pre id="browser-test-result">(.*?)</pre>',
        completed.stdout,
        re.DOTALL,
    )
    assert match is not None, completed.stdout
    observed = json.loads(html_module.unescape(match.group(1)))
    assert "error" not in observed, observed

    assert observed["initialTabs"] == {
        "count": 2,
        "searchSelected": "true",
        "searchVisible": True,
        "sourceHidden": True,
    }
    assert observed["sourceActive"] == {
        "searchHidden": True,
        "sourceVisible": True,
    }
    assert observed["preserved"] == {
        "query": "Tiger",
        "sourceUrl": "source-preserved",
    }
    assert observed["defaultPanels"] == {
        "checked": [True, True, True, False],
        "ids": ["a", "b", "c"],
    }
    assert observed["afterUncheck"] == ["a", "c"]
    assert observed["afterCheck"] == ["a", "b", "c", "d"]

    sequence = observed["sequence"]
    assert sequence["events"] == [
        "start:a",
        "poll:a:running",
        "poll:a:completed",
        "start:b",
        "poll:b:running",
        "poll:b:completed",
        "start:c",
        "poll:c:failed",
        "start:d",
        "poll:d:running",
        "poll:d:completed",
    ]
    assert sequence["snapshots"]["aPartial"] == [
        {"id": "a", "status": "處理中", "text": "A partial"},
        {"id": "b", "status": "等待中", "text": ""},
        {"id": "c", "status": "等待中", "text": ""},
        {"id": "d", "status": "等待中", "text": ""},
    ]
    assert sequence["snapshots"]["bPartial"][0] == {
        "id": "a",
        "status": "完成",
        "text": "A final",
    }
    assert sequence["snapshots"]["bPartial"][1] == {
        "id": "b",
        "status": "處理中",
        "text": "B partial",
    }
    assert sequence["requestBodies"] == [
        {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "start_time": "0:0",
            "end_time": "0:10",
            "end_time_is_default": True,
        }
        for video_id in ("a", "b", "c", "d")
    ]
    assert sequence["summary"] == "批次處理完成：3 成功，1 失敗，0 停止。"
    assert sequence["panels"] == [
        {"id": "a", "status": "完成", "text": "A final", "error": "", "errorHidden": True},
        {"id": "b", "status": "完成", "text": "B final", "error": "", "errorHidden": True},
        {
            "id": "c",
            "status": "失敗",
            "text": "",
            "error": "錯誤：whisper_transcription_failed - Local faster-whisper transcription failed.",
            "errorHidden": False,
        },
        {"id": "d", "status": "完成", "text": "D final", "error": "", "errorHidden": True},
    ]

    assert observed["allFailed"]["events"] == [
        "start:a", "poll:a:failed",
        "start:b", "poll:b:failed",
        "start:c", "poll:c:failed",
    ]
    assert observed["allFailed"]["summary"] == "批次處理完成：0 成功，3 失敗，0 停止。"
    assert observed["allFailed"]["errors"] == [
        {
            "text": "錯誤：whisper_transcription_failed - Local faster-whisper transcription failed.",
            "hidden": False,
        }
    ] * 3

    assert observed["stop"]["events"] == [
        "start:a",
        "poll:a:running",
        "stop:/api/jobs/job-a/stop",
        "poll:a:stopped",
    ]
    assert observed["stop"]["summary"] == "批次處理已停止：0 成功，0 失敗，1 已停止，2 未開始。"
    assert observed["stop"]["panels"] == [
        {"id": "a", "status": "已停止", "text": "A retained partial"},
        {"id": "b", "status": "等待中", "text": ""},
        {"id": "c", "status": "等待中", "text": ""},
    ]
