"""Configuration management.

API keys are stored via the OS keyring (Keychain on macOS, Credential Manager on
Windows). Non-secret settings live in ``~/.ai-audio/config.toml``.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import keyring
import tomli_w

KEYRING_SERVICE = "ai-audio"
KEYRING_GROQ = "groq_api_key"
KEYRING_GEMINI = "gemini_api_key"

CONFIG_DIR = Path.home() / ".ai-audio"
CONFIG_PATH = CONFIG_DIR / "config.toml"
SESSIONS_DIR = CONFIG_DIR / "sessions"

FormatMode = Literal["script", "ai_input"]


def _migrate_format_mode(value: str) -> FormatMode:
    """Map legacy values from earlier versions to the current 2-mode set."""
    if value == "ai_input":
        return "ai_input"
    # "default" / "summary" / unknown -> script (台本用)
    return "script"

DEFAULT_HOTKEY_MAC = "<cmd>+<shift>+<space>"
DEFAULT_HOTKEY_WIN = "<ctrl>+<shift>+<space>"

DEFAULT_CUSTOM_INSTRUCTIONS = (
    "私は『あ行』で始まる単語（あ・い・う・え・お で始まる単語）で吃音が出やすいです。"
    "可能な範囲で、これらの単語を同義の別の言葉に言い換えてください。"
    "例: 「明日」→「翌日」、「会う」→「面会する」、「ある」→「存在する」、"
    "「言う」→「述べる」「申す」、「行く」→「向かう」、「思う」→「考える」など。\n"
    "ただし、自然な言い換えがない場合、意味やニュアンスが変わる場合、"
    "固有名詞・数値・専門用語の場合は、原文のままにしてください。"
)


def default_hotkey() -> str:
    """Return the OS-appropriate default hotkey string (pynput format)."""
    if sys.platform == "darwin":
        return DEFAULT_HOTKEY_MAC
    return DEFAULT_HOTKEY_WIN


@dataclass
class Settings:
    """Non-secret settings persisted in config.toml."""

    hotkey: str = field(default_factory=default_hotkey)
    sample_rate: int = 16000
    channels: int = 1
    chunk_seconds: int = 150  # 2.5 min per chunk; safe under Groq 25 MB limit
    format_mode: FormatMode = "script"
    groq_model: str = "whisper-large-v3-turbo"
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_fallback_model: str = "gemini-3.1-flash-lite-preview"
    save_sessions: bool = True
    notify_on_complete: bool = True
    custom_instructions: str = field(default_factory=lambda: DEFAULT_CUSTOM_INSTRUCTIONS)
    use_custom_instructions: bool = True
    compact_window_geometry: str = ""  # "WxH+X+Y" remembered position; empty -> default

    def to_dict(self) -> dict:
        return asdict(self)


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Load settings from config.toml, falling back to defaults."""
    ensure_config_dir()
    if not CONFIG_PATH.exists():
        return Settings()
    try:
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return Settings()
    defaults = Settings()
    return Settings(
        hotkey=data.get("hotkey", defaults.hotkey),
        sample_rate=int(data.get("sample_rate", defaults.sample_rate)),
        channels=int(data.get("channels", defaults.channels)),
        chunk_seconds=int(data.get("chunk_seconds", defaults.chunk_seconds)),
        format_mode=_migrate_format_mode(data.get("format_mode", defaults.format_mode)),
        groq_model=data.get("groq_model", defaults.groq_model),
        gemini_model=data.get("gemini_model", defaults.gemini_model),
        gemini_fallback_model=data.get("gemini_fallback_model", defaults.gemini_fallback_model),
        save_sessions=bool(data.get("save_sessions", defaults.save_sessions)),
        notify_on_complete=bool(data.get("notify_on_complete", defaults.notify_on_complete)),
        custom_instructions=str(data.get("custom_instructions", defaults.custom_instructions)),
        use_custom_instructions=bool(
            data.get("use_custom_instructions", defaults.use_custom_instructions)
        ),
        compact_window_geometry=str(
            data.get("compact_window_geometry", defaults.compact_window_geometry)
        ),
    )


def save_settings(settings: Settings) -> None:
    ensure_config_dir()
    with CONFIG_PATH.open("wb") as f:
        tomli_w.dump(settings.to_dict(), f)


def get_groq_key() -> str | None:
    """Return Groq API key. Env var wins over keyring for dev override."""
    env = os.environ.get("GROQ_API_KEY")
    if env:
        return env
    return keyring.get_password(KEYRING_SERVICE, KEYRING_GROQ)


def get_gemini_key() -> str | None:
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env
    return keyring.get_password(KEYRING_SERVICE, KEYRING_GEMINI)


def set_groq_key(value: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_GROQ, value)


def set_gemini_key(value: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_GEMINI, value)


def delete_groq_key() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_GROQ)
    except keyring.errors.PasswordDeleteError:
        pass


def delete_gemini_key() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_GEMINI)
    except keyring.errors.PasswordDeleteError:
        pass
