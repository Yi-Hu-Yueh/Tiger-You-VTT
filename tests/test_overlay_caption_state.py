from app.overlay.caption_state import CaptionState


def segment(start: float, text: str) -> dict[str, object]:
    return {"start": start, "end": start + 1.0, "text": text}


def test_initial_state_uses_waiting_placeholder() -> None:
    assert CaptionState().rendered_text() == "等待字幕..."


def test_first_segment_is_displayed() -> None:
    state = CaptionState()
    assert state.update([segment(0, "第一句")]) is True
    assert state.rendered_text() == "第一句"


def test_repeated_poll_deduplicates_identical_segment() -> None:
    state = CaptionState()
    item = segment(0, "只顯示一次")
    state.update([item])
    assert state.update([item]) is False
    assert state.captions == ("只顯示一次",)


def test_new_segment_is_appended_once() -> None:
    state = CaptionState()
    state.update([segment(0, "一")])
    state.update([segment(0, "一"), segment(1, "二")])
    assert state.captions == ("一", "二")


def test_default_keeps_latest_two_meaningful_lines() -> None:
    state = CaptionState()
    state.update([segment(0, "一"), segment(1, "二"), segment(2, "三")])
    assert state.rendered_text() == "二\n三"


def test_one_line_setting_keeps_latest_line() -> None:
    state = CaptionState(1)
    state.update([segment(0, "一"), segment(1, "二")])
    assert state.rendered_text() == "二"


def test_four_line_setting_keeps_latest_four_lines() -> None:
    state = CaptionState(4)
    state.update([segment(index, str(index)) for index in range(6)])
    assert state.visible_lines() == ("2", "3", "4", "5")


def test_empty_and_invalid_segments_do_not_create_fake_text() -> None:
    state = CaptionState()
    assert state.update([segment(0, "  "), {"text": "missing times"}]) is False
    assert state.rendered_text() == "等待字幕..."


def test_silent_poll_retains_last_caption() -> None:
    state = CaptionState()
    state.update([segment(0, "保留字幕")])
    assert state.update_from_job({"segments": []}) is False
    assert state.rendered_text() == "保留字幕"


def test_terminal_job_payload_retains_last_caption() -> None:
    state = CaptionState()
    state.update([segment(0, "最後一句")])
    state.update_from_job({"status": "stopped", "segments": [segment(0, "最後一句")]})
    assert state.rendered_text() == "最後一句"
