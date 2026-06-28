"""Clipboard helper."""

from __future__ import annotations

import pyperclip


def copy(text: str) -> None:
    pyperclip.copy(text)


def paste() -> str:
    return pyperclip.paste()
