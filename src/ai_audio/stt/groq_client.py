"""Groq Whisper transcription client.

Uses ``whisper-large-v3-turbo`` by default. Notes:

- Groq enforces a 25 MB upload limit; chunking is handled in
  :mod:`ai_audio.stt.chunker` (see Step 6).
- We deliberately do NOT pass an ``initial_prompt`` to bias toward smoother
  output. Empirically, prompting Whisper to "fix" stutters increases
  hallucinations. The Gemini formatter handles cleanup downstream instead.
"""

from __future__ import annotations

import time
from pathlib import Path

from groq import Groq, RateLimitError


class GroqTranscriber:
    def __init__(self, api_key: str, model: str = "whisper-large-v3-turbo") -> None:
        self.client = Groq(api_key=api_key)
        self.model = model

    def transcribe(
        self,
        audio_path: Path,
        language: str = "ja",
        max_retries: int = 4,
    ) -> str:
        """Transcribe a single audio file. Retries on 429 with exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                with audio_path.open("rb") as f:
                    response = self.client.audio.transcriptions.create(
                        file=(audio_path.name, f.read()),
                        model=self.model,
                        language=language,
                        response_format="verbose_json",
                        temperature=0.0,
                    )
                # The Groq SDK returns either an object with .text or a dict
                if hasattr(response, "text"):
                    return response.text
                if isinstance(response, dict):
                    return response.get("text", "")
                return str(response)
            except RateLimitError as e:
                last_error = e
                if attempt >= max_retries:
                    raise
                # Honor retry-after if present, else exponential backoff.
                retry_after = _parse_retry_after(e)
                wait = retry_after if retry_after else 2 ** (attempt + 1)
                time.sleep(wait)
        # Unreachable, but mypy/ty appreciate it.
        if last_error:
            raise last_error
        return ""


def _parse_retry_after(err: RateLimitError) -> float | None:
    response = getattr(err, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
