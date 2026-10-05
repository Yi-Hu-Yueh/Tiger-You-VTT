from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
from threading import RLock
from time import monotonic
from typing import Any

from app.config import DIARIZATION_SETTINGS


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeakerRegion:
    start: float
    end: float
    speaker: str


_pipeline: Any | None = None
_pipeline_lock = RLock()
_DIARIZATION_SAMPLE_RATE = 16_000


def _load_pipeline() -> Any:
    global _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        try:
            import torch
            from pyannote.audio import Pipeline

            pipeline = Pipeline.from_pretrained(
                DIARIZATION_SETTINGS.model, token=True
            )
            pipeline.to(torch.device(DIARIZATION_SETTINGS.device))
        except Exception as exc:
            raise RuntimeError("diarization_model_load_failed") from exc
        _pipeline = pipeline
        return pipeline


def _annotation_from_output(output: Any) -> Any:
    for attribute in (
        "exclusive_speaker_diarization",
        "speaker_diarization",
    ):
        annotation = getattr(output, attribute, None)
        if annotation is not None:
            return annotation
    return output


def _decode_waveform(audio_path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(audio_path),
                "-f",
                "f32le",
                "-acodec",
                "pcm_f32le",
                "-ac",
                "1",
                "-ar",
                str(_DIARIZATION_SAMPLE_RATE),
                "pipe:1",
            ],
            capture_output=True,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("diarization_audio_decode_failed") from exc
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError("diarization_audio_decode_failed")

    try:
        import torch

        waveform = torch.frombuffer(
            bytearray(completed.stdout), dtype=torch.float32
        ).reshape(1, -1)
    except Exception as exc:
        raise RuntimeError("diarization_audio_decode_failed") from exc
    return {"waveform": waveform, "sample_rate": _DIARIZATION_SAMPLE_RATE}


def diarize_audio(audio_path: Path) -> tuple[list[SpeakerRegion], float]:
    pipeline = _load_pipeline()
    started_at = monotonic()
    try:
        audio = _decode_waveform(audio_path)
        with _pipeline_lock:
            output = pipeline(audio)
        annotation = _annotation_from_output(output)
        regions = [
            SpeakerRegion(float(turn.start), float(turn.end), str(label))
            for turn, _, label in annotation.itertracks(yield_label=True)
            if float(turn.end) > float(turn.start)
        ]
    except Exception as exc:
        raise RuntimeError("diarization_inference_failed") from exc
    return sorted(regions, key=lambda item: (item.start, item.end)), (
        monotonic() - started_at
    )


def normalize_speakers(
    regions: Sequence[SpeakerRegion], *, offset: float = 0.0
) -> list[SpeakerRegion]:
    labels: dict[str, str] = {}
    normalized: list[SpeakerRegion] = []
    for region in sorted(regions, key=lambda item: (item.start, item.end)):
        if region.speaker not in labels:
            labels[region.speaker] = f"Speaker {len(labels) + 1}"
        normalized.append(
            SpeakerRegion(
                start=region.start + offset,
                end=region.end + offset,
                speaker=labels[region.speaker],
            )
        )
    return normalized


def align_speakers(
    segments: Sequence[Mapping[str, Any]],
    regions: Sequence[SpeakerRegion],
) -> list[dict[str, Any]]:
    aligned: list[dict[str, Any]] = []
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        overlaps: dict[str, float] = {}
        for region in regions:
            overlap = max(0.0, min(end, region.end) - max(start, region.start))
            if overlap > 0:
                overlaps[region.speaker] = overlaps.get(region.speaker, 0.0) + overlap
        speaker = (
            max(overlaps, key=lambda label: overlaps[label]) if overlaps else None
        )
        aligned.append(
            {
                "start": start,
                "end": end,
                "text": str(segment.get("text", "")),
                "speaker": speaker,
            }
        )
    return aligned


def _timestamp(seconds: float, separator: str) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return (
        f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
        f"{separator}{milliseconds:03d}"
    )


def _speaker_label(segment: Mapping[str, Any]) -> str:
    return str(segment.get("speaker") or "Unknown")


def _speaker_caption(segment: Mapping[str, Any]) -> str:
    speaker = segment.get("speaker") or "Unknown"
    return f"[{speaker}] {str(segment.get('text', '')).strip()}"


def speaker_transcript_to_txt(segments: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(
        f"{_speaker_label(segment)}：{str(segment.get('text', '')).strip()}"
        for segment in segments
    )


def speaker_transcript_to_vtt(segments: Sequence[Mapping[str, Any]]) -> str:
    blocks = ["WEBVTT", ""]
    for segment in segments:
        blocks.extend(
            [
                f"{_timestamp(float(segment['start']), '.')} --> "
                f"{_timestamp(float(segment['end']), '.')}",
                _speaker_caption(segment),
                "",
            ]
        )
    return "\n".join(blocks)


def speaker_transcript_to_srt(segments: Sequence[Mapping[str, Any]]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        blocks.extend(
            [
                str(index),
                f"{_timestamp(float(segment['start']), ',')} --> "
                f"{_timestamp(float(segment['end']), ',')}",
                _speaker_caption(segment),
                "",
            ]
        )
    return "\n".join(blocks)


def diarization_failure_result(
    result: Mapping[str, Any], code: str, message: str
) -> dict[str, Any]:
    augmented = dict(result)
    augmented.update(
        {
            "diarization_enabled": True,
            "diarization_status": "failed",
            "diarization_error": {"code": code, "message": message},
        }
    )
    return augmented


def diarization_stopped_result(result: Mapping[str, Any]) -> dict[str, Any]:
    augmented = dict(result)
    augmented.update(
        {
            "diarization_enabled": True,
            "diarization_status": "stopped",
            "_stopped": True,
        }
    )
    return augmented


def apply_diarization(
    result: Mapping[str, Any],
    audio_path: Path,
    *,
    timestamp_offset: float = 0.0,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    augmented = dict(result)
    if bool(augmented.get("_stopped")) or (
        should_stop is not None and should_stop()
    ):
        return diarization_stopped_result(augmented)
    if not audio_path.is_file():
        return diarization_failure_result(
            augmented,
            "diarization_audio_unavailable",
            "Speaker diarization could not access the temporary audio.",
        )

    try:
        raw_regions, duration = diarize_audio(audio_path)
        regions = normalize_speakers(raw_regions, offset=timestamp_offset)
        segments = augmented.get("segments")
        if not isinstance(segments, list):
            raise RuntimeError("diarization_transcript_unavailable")
        diarized_segments = align_speakers(segments, regions)
    except RuntimeError as exc:
        code = str(exc)
        if code == "diarization_model_load_failed":
            message = "The local speaker diarization model could not be loaded."
        elif code == "diarization_transcript_unavailable":
            message = "The transcript was unavailable for speaker alignment."
        else:
            code = "diarization_inference_failed"
            message = "Speaker diarization could not be completed."
        logger.exception("Optional speaker diarization failed with %s", code)
        return diarization_failure_result(augmented, code, message)
    except Exception:
        logger.exception("Optional speaker diarization failed unexpectedly")
        return diarization_failure_result(
            augmented,
            "diarization_inference_failed",
            "Speaker diarization could not be completed.",
        )

    speakers = {region.speaker for region in regions}
    augmented.update(
        {
            "diarization_enabled": True,
            "diarization_status": "completed",
            "speaker_count": len(speakers),
            "diarized_segments": diarized_segments,
            "speaker_txt": speaker_transcript_to_txt(diarized_segments),
            "speaker_vtt": speaker_transcript_to_vtt(diarized_segments),
            "speaker_srt": speaker_transcript_to_srt(diarized_segments),
            "diarization_model": DIARIZATION_SETTINGS.model,
            "diarization_device": DIARIZATION_SETTINGS.device,
            "diarization_duration": round(duration, 3),
        }
    )
    if should_stop is not None and should_stop():
        augmented["_stopped"] = True
    return augmented
