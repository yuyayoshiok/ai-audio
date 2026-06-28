"""Session persistence.

Each recording session is stored under
``~/.ai-audio/sessions/YYYY-MM-DD_HHMMSS/``::

    audio.wav        # raw 16 kHz mono PCM
    raw.txt          # raw Whisper output (preserved even if formatting fails)
    formatted.txt    # post-Gemini text
    meta.json        # mode, models, durations, timings

The raw transcript is the durable artifact — even if Gemini hallucinates or
fails, the user always has the unmodified Whisper output to fall back on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_audio.audio.recorder import RecordingResult, write_wav
from ai_audio.config import SESSIONS_DIR


@dataclass
class SessionPaths:
    root: Path
    audio: Path
    raw: Path
    formatted: Path
    meta: Path


def new_session_dir(now: datetime | None = None) -> SessionPaths:
    now = now or datetime.now()
    name = now.strftime("%Y-%m-%d_%H%M%S")
    root = SESSIONS_DIR / name
    root.mkdir(parents=True, exist_ok=True)
    return SessionPaths(
        root=root,
        audio=root / "audio.wav",
        raw=root / "raw.txt",
        formatted=root / "formatted.txt",
        meta=root / "meta.json",
    )


def save_audio(paths: SessionPaths, result: RecordingResult) -> None:
    write_wav(paths.audio, result)


def save_raw(paths: SessionPaths, raw_text: str) -> None:
    paths.raw.write_text(raw_text, encoding="utf-8")


def save_formatted(paths: SessionPaths, formatted_text: str) -> None:
    paths.formatted.write_text(formatted_text, encoding="utf-8")


def save_meta(paths: SessionPaths, meta: dict) -> None:
    paths.meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
