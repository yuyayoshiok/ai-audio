"""Global hotkey listener (pynput).

Runs a daemon thread that listens for a single hotkey combination (e.g.
``<cmd>+<shift>+<space>``). When the hotkey is pressed, the registered
callback is invoked **on the listener thread** — the caller is responsible
for marshalling onto the UI thread (e.g. ``Tk.after(0, fn)``).

macOS notes
-----------
Global hotkey listening requires Accessibility permission on macOS. The first
time the app runs, the OS will prompt the user to grant access for the
terminal/Python binary that's running. Without it, ``pynput`` silently fails
to receive key events.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from pynput import keyboard

log = logging.getLogger(__name__)


class HotkeyListener:
    """Owns a single global hotkey + listener thread."""

    def __init__(self) -> None:
        self._hotkeys: keyboard.GlobalHotKeys | None = None
        self._lock = threading.Lock()
        self._current_combo: str | None = None
        self._error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._error

    @property
    def active_combo(self) -> str | None:
        return self._current_combo

    def start(self, combo: str, callback: Callable[[], None]) -> bool:
        """Register ``combo`` and start listening. Replaces any previous combo.

        Returns ``True`` on success, ``False`` if the combo could not be
        parsed or the listener could not start.
        """
        with self._lock:
            self._error = None
            self.stop_locked()
            try:
                self._hotkeys = keyboard.GlobalHotKeys({combo: callback})
                self._hotkeys.daemon = True
                self._hotkeys.start()
                self._current_combo = combo
                return True
            except Exception as e:  # noqa: BLE001
                log.exception("Failed to start hotkey listener for %r: %s", combo, e)
                self._error = str(e)
                self._hotkeys = None
                self._current_combo = None
                return False

    def stop(self) -> None:
        with self._lock:
            self.stop_locked()

    def stop_locked(self) -> None:
        """Stop the listener (caller must hold the lock)."""
        if self._hotkeys is None:
            return
        try:
            self._hotkeys.stop()
        except Exception:
            log.exception("Error while stopping hotkey listener")
        self._hotkeys = None
        self._current_combo = None
