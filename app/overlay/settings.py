from dataclasses import dataclass, replace
from typing import Any, Literal


PositionMode = Literal["top", "bottom", "custom"]


@dataclass(frozen=True)
class OverlaySettings:
    position: PositionMode = "bottom"
    font_size: int = 32
    background_opacity: int = 70
    display_lines: int = 2
    always_on_top: bool = True

    def validated(self) -> "OverlaySettings":
        position: PositionMode = (
            self.position
            if self.position in {"top", "bottom", "custom"}
            else "bottom"
        )
        return replace(
            self,
            position=position,
            font_size=min(72, max(18, int(self.font_size))),
            background_opacity=min(
                95, max(20, int(self.background_opacity))
            ),
            display_lines=min(4, max(1, int(self.display_lines))),
            always_on_top=bool(self.always_on_top),
        )

    @classmethod
    def from_store(cls, store: Any) -> "OverlaySettings":
        return cls(
            position=str(store.value("position", "bottom")),
            font_size=int(store.value("font_size", 32)),
            background_opacity=int(store.value("background_opacity", 70)),
            display_lines=int(store.value("display_lines", 2)),
            always_on_top=str(
                store.value("always_on_top", "true")
            ).casefold()
            in {"1", "true", "yes"},
        ).validated()

    def save(self, store: Any) -> None:
        store.setValue("position", self.position)
        store.setValue("font_size", self.font_size)
        store.setValue("background_opacity", self.background_opacity)
        store.setValue("display_lines", self.display_lines)
        store.setValue("always_on_top", self.always_on_top)

