"""Compact recording window (Superwhisper-style).

A small borderless always-on-top window shown while the user is recording.
Displays a record indicator, elapsed time, a live level meter, and a stop
button. The window is draggable; its position is remembered between sessions.

The window does not poll audio levels itself — the caller (MainWindow) drives
``update_level()`` and ``update_timer()`` via Tk ``after()`` callbacks so all
widget updates happen on the Tk thread.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

import customtkinter as ctk

WINDOW_WIDTH = 320
WINDOW_HEIGHT = 56
NUM_BARS = 24


class LevelMeter(tk.Canvas):
    """Sliding-window level meter with NUM_BARS vertical bars.

    Uses plain ``tkinter.Canvas`` (not ``ctk.CTkCanvas``) — CTkCanvas's
    constructor signature can mangle the widget path on macOS when width/height
    are passed as kwargs, leading to ``invalid command name "<width>"`` errors
    on the first ``delete("all")`` call. We don't need any CTk features here
    since we draw the bars ourselves.
    """

    def __init__(self, master, width: int, height: int, num_bars: int = NUM_BARS) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg="#111111",
        )
        self.num_bars = num_bars
        self.history: list[float] = [0.0] * num_bars
        # IMPORTANT: do NOT name these ``self._w`` / ``self._h`` —
        # ``self._w`` is reserved by tkinter for the widget's Tcl path, and
        # overwriting it makes every subsequent tk.call (including delete())
        # fail with "invalid command name '<width>'".
        self._canvas_w = width
        self._canvas_h = height
        self._redraw()

    def push(self, level: float) -> None:
        """Append a new level (0.0 - 1.0) and redraw.

        ``level`` is a peak value (max abs sample). Boost factor 4.5x gives
        normal speech peaks (~0.08-0.2) a working range that crosses the color
        thresholds (cyan -> green -> yellow), so the meter visibly changes
        shade when the user is speaking.
        """
        boosted = min(1.0, level * 4.5)
        self.history.append(boosted)
        if len(self.history) > self.num_bars:
            self.history = self.history[-self.num_bars :]
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        bar_w = self._canvas_w / self.num_bars
        center_y = self._canvas_h / 2
        for i, lvl in enumerate(self.history):
            x = i * bar_w
            bar_h = max(2.0, lvl * (self._canvas_h - 4))
            y0 = center_y - bar_h / 2
            y1 = center_y + bar_h / 2
            color = self._color_for(lvl)
            self.create_rectangle(
                x + 1, y0, x + bar_w - 1, y1, fill=color, outline=""
            )

    @staticmethod
    def _color_for(lvl: float) -> str:
        # Cool to warm gradient: cyan -> green -> yellow -> orange.
        if lvl < 0.25:
            return "#3aa6c8"
        if lvl < 0.55:
            return "#3acf6e"
        if lvl < 0.8:
            return "#e8c14a"
        return "#ef6f3c"


class CompactWindow(ctk.CTkToplevel):
    """Borderless mini-window shown during recording."""

    def __init__(
        self,
        master,
        on_stop: Callable[[], None],
        initial_geometry: str = "",
    ) -> None:
        super().__init__(master)
        self._on_stop = on_stop
        self._closing = False

        # Borderless, always-on-top.
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            # macOS niceties: keep above fullscreen apps & don't show in dock.
            self.attributes("-type", "utility")
        except Exception:
            pass

        # Geometry: previous position if remembered, otherwise bottom-center.
        self._set_initial_geometry(initial_geometry)

        # Rounded-ish dark frame.
        self.configure(fg_color="#1c1c1e")
        self.frame = ctk.CTkFrame(self, fg_color="#1c1c1e", corner_radius=12)
        self.frame.pack(fill="both", expand=True, padx=2, pady=2)

        # Layout: ●REC | timer | level meter | stop
        self.rec_dot = ctk.CTkLabel(
            self.frame,
            text="●",
            text_color="#ff453a",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.rec_dot.pack(side="left", padx=(10, 4))

        self.timer_label = ctk.CTkLabel(
            self.frame,
            text="00:00",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f5f5f7",
        )
        self.timer_label.pack(side="left", padx=(0, 8))

        self.level_meter = LevelMeter(self.frame, width=160, height=36)
        self.level_meter.pack(side="left", padx=4)

        self.stop_btn = ctk.CTkButton(
            self.frame,
            text="■",
            width=36,
            height=36,
            fg_color="#3a3a3c",
            hover_color="#48484a",
            text_color="#f5f5f7",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._handle_stop,
        )
        self.stop_btn.pack(side="right", padx=(8, 8))

        # Drag handling: clicking anywhere on the window (except the stop
        # button) lets the user drag it around.
        self._drag_offset = (0, 0)
        for w in (self, self.frame, self.rec_dot, self.timer_label, self.level_meter):
            w.bind("<Button-1>", self._on_drag_start)
            w.bind("<B1-Motion>", self._on_drag_motion)

        self._blink_state = True
        self._blink_job: str | None = None
        self._start_blink()

    # ------------------------------------------------------------------ public

    def update_level(self, level: float) -> None:
        if self._closing:
            return
        self.level_meter.push(level)

    def update_timer(self, seconds: int) -> None:
        if self._closing:
            return
        mm, ss = divmod(int(seconds), 60)
        self.timer_label.configure(text=f"{mm:02d}:{ss:02d}")

    def get_geometry_string(self) -> str:
        try:
            return self.geometry()
        except Exception:
            return ""

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._blink_job:
            try:
                self.after_cancel(self._blink_job)
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ internals

    def _set_initial_geometry(self, remembered: str) -> None:
        if remembered:
            try:
                self.geometry(remembered)
                return
            except Exception:
                pass
        # Default: bottom-center of the primary screen.
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = (screen_w - WINDOW_WIDTH) // 2
        y = screen_h - WINDOW_HEIGHT - 96
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def _on_drag_start(self, event) -> None:
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _on_drag_motion(self, event) -> None:
        ox, oy = self._drag_offset
        new_x = event.x_root - ox
        new_y = event.y_root - oy
        self.geometry(f"+{new_x}+{new_y}")

    def _handle_stop(self) -> None:
        try:
            self._on_stop()
        finally:
            # Caller decides when to actually destroy the window.
            pass

    def _start_blink(self) -> None:
        if self._closing:
            return
        self._blink_state = not self._blink_state
        self.rec_dot.configure(text_color="#ff453a" if self._blink_state else "#7a1f1c")
        self._blink_job = self.after(500, self._start_blink)
