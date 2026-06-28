"""High-level pipeline: record -> transcribe -> format -> clipboard.

This module wires the audio, STT, LLM, and storage components together. It is
intentionally synchronous; the tray UI (Step 5) will run it on a worker thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_audio.audio.recorder import RecordingResult
from ai_audio.config import Settings, get_gemini_key, get_groq_key
from ai_audio.desktop import clipboard, notifications
from ai_audio.llm.gemini_client import GeminiFormatter
from ai_audio.llm.prompts import FormatMode
from ai_audio.storage import sessions
from ai_audio.stt.groq_client import GroqTranscriber

log = logging.getLogger(__name__)


class MissingApiKeyError(RuntimeError):
    pass


@dataclass
class PipelineResult:
    raw_text: str
    formatted_text: str
    session_root: Path
    duration_seconds: float
    used_fallback: bool = False


def process(
    recording: RecordingResult,
    settings: Settings,
    mode: FormatMode | None = None,
) -> PipelineResult:
    """Run the full pipeline for one recording.

    The raw transcript is saved before formatting, so a Gemini failure never
    loses the user's words. On formatting failure, the raw text is copied to
    the clipboard as a fallback.
    """
    groq_key = get_groq_key()
    gemini_key = get_gemini_key()

    if not groq_key:
        raise MissingApiKeyError("Groq API key not set. Run: ai-audio config set-key groq")
    if not gemini_key:
        raise MissingApiKeyError("Gemini API key not set. Run: ai-audio config set-key gemini")

    chosen_mode: FormatMode = mode or settings.format_mode
    paths = sessions.new_session_dir()

    if settings.save_sessions:
        sessions.save_audio(paths, recording)

    # 1. Transcribe.
    transcriber = GroqTranscriber(api_key=groq_key, model=settings.groq_model)
    raw_text = transcriber.transcribe(paths.audio, language="ja")
    if settings.save_sessions:
        sessions.save_raw(paths, raw_text)

    # 2. Format. Failures must never lose the raw transcript.
    formatter = GeminiFormatter(
        api_key=gemini_key,
        model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
    )
    formatted_text = raw_text
    used_fallback = False
    custom = settings.custom_instructions if settings.use_custom_instructions else None
    try:
        formatted_text = formatter.format(
            raw_text, mode=chosen_mode, custom_instructions=custom
        )
        if not formatted_text.strip():
            formatted_text = raw_text
            used_fallback = True
    except Exception as e:  # noqa: BLE001
        log.exception("Gemini formatting failed; falling back to raw transcript: %s", e)
        formatted_text = raw_text
        used_fallback = True

    if settings.save_sessions:
        sessions.save_formatted(paths, formatted_text)
        sessions.save_meta(
            paths,
            {
                "timestamp": datetime.now().isoformat(),
                "mode": chosen_mode,
                "duration_seconds": recording.duration_seconds,
                "groq_model": settings.groq_model,
                "gemini_model": settings.gemini_model,
                "used_fallback": used_fallback,
            },
        )

    # 3. Copy to clipboard.
    clipboard.copy(formatted_text)

    if settings.notify_on_complete:
        char_count = len(formatted_text)
        title = "ai-audio: コピー完了"
        if used_fallback:
            title = "ai-audio: 整形失敗 → 生テキストをコピー"
        notifications.notify(title, f"{char_count}文字 / {recording.duration_seconds:.1f}秒")

    return PipelineResult(
        raw_text=raw_text,
        formatted_text=formatted_text,
        session_root=paths.root,
        duration_seconds=recording.duration_seconds,
        used_fallback=used_fallback,
    )
