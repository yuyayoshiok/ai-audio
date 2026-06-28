"""macOS-specific helpers (Accessibility permission, app activation).

On macOS, global hotkey listening via pynput requires the process running
Python to have **Accessibility** permission. Without it, pynput silently
fails to receive events even though it appears to register the hotkey.

We expose helpers:

- :func:`is_accessibility_trusted` — whether this process has it.
- :func:`request_accessibility_with_prompt` — triggers Apple's standard
  permission prompt (which has a built-in "Open System Settings" button).
- :func:`open_accessibility_settings` — opens the Accessibility pane.
- :func:`current_python_path` — the Python binary path (so the user can add
  it to the Accessibility list manually).
- :func:`current_terminal_name` — best-effort detection of the parent
  terminal (Terminal / iTerm / Warp / VS Code) so we can tell the user
  which app to add.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

ACCESSIBILITY_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_accessibility_trusted() -> bool:
    """Whether this process has macOS Accessibility permission.

    Returns ``True`` on non-macOS platforms (concept doesn't apply).
    """
    if not is_macos():
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        log.debug("AXIsProcessTrusted unavailable", exc_info=True)
        # If we can't check, optimistically return True so we don't pester
        # users on systems where the framework isn't available.
        return True


def request_accessibility_with_prompt() -> bool:
    """Trigger Apple's standard Accessibility-access dialog if needed.

    The dialog includes an "Open System Settings" button that takes the user
    straight to the right pane. After the user grants permission, the app
    must be **restarted** for it to take effect (macOS doesn't grant trust
    retroactively to already-running processes).

    Returns ``True`` if the process is currently trusted; ``False`` if it's
    not trusted (and the prompt was shown to the user).
    """
    if not is_macos():
        return True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import (
            CFDictionaryCreate,
            kCFBooleanTrue,
            kCFTypeDictionaryKeyCallBacks,
            kCFTypeDictionaryValueCallBacks,
        )

        prompt_key = "AXTrustedCheckOptionPrompt"
        options = CFDictionaryCreate(
            None,
            (prompt_key,),
            (kCFBooleanTrue,),
            1,
            kCFTypeDictionaryKeyCallBacks,
            kCFTypeDictionaryValueCallBacks,
        )
        return bool(AXIsProcessTrustedWithOptions(options))
    except Exception:
        log.exception("Failed to request accessibility prompt")
        return is_accessibility_trusted()


def open_accessibility_settings() -> None:
    """Open the Accessibility pane in System Settings (macOS only)."""
    if not is_macos():
        return
    try:
        subprocess.Popen(["open", ACCESSIBILITY_URL])
    except Exception:
        log.exception("Failed to open Accessibility settings")


def current_python_path() -> str:
    """Path to the Python binary running this process.

    On macOS, this is the binary the user needs to add to the Accessibility
    list (via the '+' button in System Settings) when ``uv run`` is used —
    the prompt API often fails to auto-register uv-spawned processes.
    """
    return sys.executable


def current_terminal_name() -> str:
    """Best-effort name of the terminal app that launched this process.

    Returns one of: ``"Terminal"``, ``"iTerm"``, ``"Warp"``, ``"VS Code"``,
    ``"Cursor"``, ``"Hyper"``, ``"WezTerm"``, ``"Alacritty"``, or
    ``"unknown terminal"``.
    """
    if not is_macos():
        return "unknown terminal"
    term = os.environ.get("TERM_PROGRAM", "")
    mapping = {
        "Apple_Terminal": "Terminal",
        "iTerm.app": "iTerm",
        "WarpTerminal": "Warp",
        "vscode": "VS Code",
        "Cursor": "Cursor",
        "Hyper": "Hyper",
        "WezTerm": "WezTerm",
        "Alacritty": "Alacritty",
    }
    return mapping.get(term, term or "unknown terminal")


def reveal_in_finder(path: str) -> None:
    """Open Finder and select the given path."""
    if not is_macos():
        return
    try:
        subprocess.Popen(["open", "-R", path])
    except Exception:
        log.exception("Failed to reveal path in Finder")
