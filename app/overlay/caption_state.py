from collections.abc import Mapping, Sequence
from typing import Any


class CaptionState:
    def __init__(self, display_lines: int = 2) -> None:
        self._display_lines = 2
        self.display_lines = display_lines
        self._seen: set[tuple[float, float, str]] = set()
        self._captions: list[str] = []

    @property
    def display_lines(self) -> int:
        return self._display_lines

    @display_lines.setter
    def display_lines(self, value: int) -> None:
        self._display_lines = min(4, max(1, int(value)))

    @property
    def captions(self) -> tuple[str, ...]:
        return tuple(self._captions)

    def update(self, segments: Sequence[Mapping[str, Any]]) -> bool:
        changed = False
        for segment in segments:
            text = str(segment.get("text", "")).strip()
            if not text:
                continue
            try:
                key = (
                    float(segment["start"]),
                    float(segment["end"]),
                    text,
                )
            except (KeyError, TypeError, ValueError):
                continue
            if key in self._seen:
                continue
            self._seen.add(key)
            self._captions.append(text)
            changed = True
        return changed

    def update_from_job(self, job: Mapping[str, Any]) -> bool:
        segments = job.get("segments")
        return self.update(segments if isinstance(segments, list) else [])

    def visible_lines(self) -> tuple[str, ...]:
        return tuple(self._captions[-self.display_lines :])

    def rendered_text(self) -> str:
        lines = self.visible_lines()
        return "\n".join(lines) if lines else "等待字幕..."

    def clear(self) -> None:
        self._seen.clear()
        self._captions.clear()

