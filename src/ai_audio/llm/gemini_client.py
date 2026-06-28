"""Gemini formatting client.

Uses the new ``google-genai`` SDK (the unified client for Google AI Studio and
Vertex AI). The primary model is ``gemini-3.1-flash-lite``; if that ID is
unavailable we fall back to ``gemini-3.1-flash-lite-preview``.
"""

from __future__ import annotations

from google import genai
from google.genai import types

from ai_audio.llm.prompts import FormatMode, build_system_prompt, user_prompt


class GeminiFormatter:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.1-flash-lite",
        fallback_model: str = "gemini-3.1-flash-lite-preview",
    ) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.fallback_model = fallback_model

    def format(
        self,
        transcript: str,
        mode: FormatMode = "default",
        custom_instructions: str | None = None,
    ) -> str:
        if not transcript.strip():
            return ""

        system_prompt = build_system_prompt(mode, custom_instructions)
        user_text = user_prompt(transcript)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
            # Output budget — meeting scripts can be long.
            max_output_tokens=8192,
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_text,
                config=config,
            )
        except Exception:
            # Fall back to preview model if the stable ID is not yet available.
            response = self.client.models.generate_content(
                model=self.fallback_model,
                contents=user_text,
                config=config,
            )

        return _extract_text(response)


def _extract_text(response) -> str:
    """Best-effort text extraction across SDK versions."""
    text = getattr(response, "text", None)
    if text:
        return text.strip()
    candidates = getattr(response, "candidates", None) or []
    chunks: list[str] = []
    for cand in candidates:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", []) or []:
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(piece)
    return "".join(chunks).strip()
