"""Main GUI window (customtkinter).

Three tabs: 録音 (recording), 履歴 (history), 設定 (settings).

Threading model:
- Main thread runs the Tk event loop.
- ``Recorder`` runs its own audio callback thread (already isolated).
- The transcribe/format pipeline runs on a ``threading.Thread`` worker; results
  are posted back via ``root.after(0, ...)`` so all widget updates happen on
  the Tk thread.
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from ai_audio import config as cfg
from ai_audio import controller
from ai_audio.audio.recorder import Recorder, RecordingResult
from ai_audio.desktop import clipboard
from ai_audio.desktop import macos as macos_helpers
from ai_audio.gui.compact_window import CompactWindow
from ai_audio.hotkey.listener import HotkeyListener
from ai_audio.llm.prompts import FormatMode

log = logging.getLogger(__name__)

MODE_LABELS: dict[FormatMode, str] = {
    "script": "台本用",
    "ai_input": "AI入力用",
}
MODE_REVERSE: dict[str, FormatMode] = {v: k for k, v in MODE_LABELS.items()}


class MainWindow(ctk.CTk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.title("ai-audio")
        self.geometry("780x620")
        self.minsize(640, 520)

        self.settings = cfg.load_settings()
        self.recorder = Recorder(
            sample_rate=self.settings.sample_rate,
            channels=self.settings.channels,
        )

        self._is_processing = False
        self._timer_job: str | None = None
        self._level_poll_job: str | None = None
        self._record_started_at: float | None = None
        self._last_result: controller.PipelineResult | None = None
        self._compact_window: CompactWindow | None = None
        self._hotkey_listener = HotkeyListener()

        # Cross-thread UI event queue. Background workers push callables here;
        # the main thread polls and runs them. Using ``Tk.after(0, ...)`` from
        # a background thread is not actually thread-safe on macOS — it works
        # the first time but can deadlock on subsequent invocations.
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        self._build_ui()
        self._refresh_status_idle()
        self._start_hotkey_listener()
        # Defer the accessibility check so the main window paints first.
        self.after(400, self._check_accessibility)
        # Start the cross-thread UI event pump.
        self.after(50, self._pump_ui_queue)

        # Hide instead of quitting when the close button is pressed (Step 5
        # will wire this to the tray). For now, the window does still close.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=12)

        self.tabs.add("録音")
        self.tabs.add("履歴")
        self.tabs.add("設定")

        self._build_recording_tab(self.tabs.tab("録音"))
        self._build_history_tab(self.tabs.tab("履歴"))
        self._build_settings_tab(self.tabs.tab("設定"))

    # ---- Recording tab ------------------------------------------------

    def _build_recording_tab(self, parent: ctk.CTkFrame) -> None:
        # Top bar: record button + mode + timer + status
        top = ctk.CTkFrame(parent)
        top.pack(fill="x", padx=4, pady=(4, 8))

        self.record_btn = ctk.CTkButton(
            top,
            text="● 録音開始",
            width=140,
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#c0392b",
            hover_color="#a93226",
            command=self.toggle_recording,
        )
        self.record_btn.pack(side="left", padx=(8, 12), pady=8)

        ctk.CTkLabel(top, text="モード:").pack(side="left", padx=(8, 4))
        self.mode_var = ctk.StringVar(value=MODE_LABELS[self.settings.format_mode])
        self.mode_menu = ctk.CTkOptionMenu(
            top,
            values=list(MODE_LABELS.values()),
            variable=self.mode_var,
            width=120,
            command=self._on_mode_change,
        )
        self.mode_menu.pack(side="left", padx=(0, 12))

        self.timer_label = ctk.CTkLabel(top, text="00:00", font=ctk.CTkFont(size=18, weight="bold"))
        self.timer_label.pack(side="right", padx=12)

        self.status_label = ctk.CTkLabel(top, text="待機中", text_color="gray")
        self.status_label.pack(side="right", padx=8)

        # Result section
        result_frame = ctk.CTkFrame(parent)
        result_frame.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(
            result_frame,
            text="整形結果（クリップボードへ自動コピー済み）",
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 4))

        self.result_text = ctk.CTkTextbox(result_frame, wrap="word", font=ctk.CTkFont(size=14))
        self.result_text.pack(fill="both", expand=True, padx=8, pady=4)

        # Action buttons
        actions = ctk.CTkFrame(result_frame, fg_color="transparent")
        actions.pack(fill="x", padx=4, pady=(4, 8))

        ctk.CTkButton(actions, text="再コピー", width=100, command=self._copy_result).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions,
            text="編集後をコピー",
            width=140,
            command=self._copy_edited,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions,
            text="再整形",
            width=100,
            command=self._reformat_last,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions,
            text="クリア",
            width=80,
            fg_color="gray40",
            hover_color="gray30",
            command=self._clear_result,
        ).pack(side="right", padx=4)

        # Raw transcript (collapsible)
        self._raw_visible = False
        self.toggle_raw_btn = ctk.CTkButton(
            parent,
            text="▶ 生文字起こしを表示",
            anchor="w",
            fg_color="transparent",
            text_color=("gray20", "gray80"),
            hover_color=("gray85", "gray25"),
            command=self._toggle_raw,
        )
        self.toggle_raw_btn.pack(fill="x", padx=4, pady=(8, 0))

        self.raw_text = ctk.CTkTextbox(parent, height=120, wrap="word")
        # Not packed yet; toggled on demand.

    def _toggle_raw(self) -> None:
        self._raw_visible = not self._raw_visible
        if self._raw_visible:
            self.raw_text.pack(fill="x", padx=8, pady=(0, 8))
            self.toggle_raw_btn.configure(text="▼ 生文字起こしを隠す")
        else:
            self.raw_text.pack_forget()
            self.toggle_raw_btn.configure(text="▶ 生文字起こしを表示")

    def _on_mode_change(self, label: str) -> None:
        self.settings.format_mode = MODE_REVERSE[label]
        cfg.save_settings(self.settings)

    # ---- History tab --------------------------------------------------

    def _build_history_tab(self, parent: ctk.CTkFrame) -> None:
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 4))
        ctk.CTkLabel(
            header,
            text="セッション履歴",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            header,
            text="すべて削除",
            width=110,
            fg_color="#a93226",
            hover_color="#7b241c",
            command=self._delete_all_sessions,
        ).pack(side="right", padx=4)
        ctk.CTkButton(header, text="↻ 更新", width=80, command=self._refresh_history).pack(
            side="right", padx=4
        )

        self.history_list = ctk.CTkScrollableFrame(parent, label_text="")
        self.history_list.pack(fill="both", expand=True, padx=4, pady=4)

        self._refresh_history()

    def _refresh_history(self) -> None:
        for child in self.history_list.winfo_children():
            child.destroy()

        if not cfg.SESSIONS_DIR.exists():
            ctk.CTkLabel(self.history_list, text="（履歴なし）", text_color="gray").pack(pady=20)
            return

        sessions_dirs = sorted(
            [p for p in cfg.SESSIONS_DIR.iterdir() if p.is_dir()],
            reverse=True,
        )
        if not sessions_dirs:
            ctk.CTkLabel(self.history_list, text="（履歴なし）", text_color="gray").pack(pady=20)
            return

        for session_dir in sessions_dirs[:80]:
            self._add_history_row(session_dir)

    def _add_history_row(self, session_dir: Path) -> None:
        meta_path = session_dir / "meta.json"
        formatted_path = session_dir / "formatted.txt"

        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}

        preview = ""
        if formatted_path.exists():
            try:
                preview = formatted_path.read_text(encoding="utf-8").strip().replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:80] + "…"
            except OSError:
                preview = ""

        title = session_dir.name
        if meta.get("timestamp"):
            try:
                dt = datetime.fromisoformat(meta["timestamp"])
                title = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass

        mode = meta.get("mode", "?")
        duration = meta.get("duration_seconds")
        meta_line = f"  モード: {MODE_LABELS.get(mode, mode)}"
        if duration is not None:
            meta_line += f"   {duration:.1f}秒"
        if meta.get("used_fallback"):
            meta_line += "   [整形失敗]"

        row = ctk.CTkFrame(self.history_list)
        row.pack(fill="x", padx=4, pady=2)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        ctk.CTkLabel(info, text=title, anchor="w", font=ctk.CTkFont(size=13, weight="bold")).pack(
            fill="x"
        )
        ctk.CTkLabel(info, text=meta_line, anchor="w", text_color="gray").pack(fill="x")
        if preview:
            ctk.CTkLabel(info, text=preview, anchor="w", wraplength=520).pack(fill="x")

        btn_col = ctk.CTkFrame(row, fg_color="transparent")
        btn_col.pack(side="right", padx=4)
        ctk.CTkButton(
            btn_col,
            text="開く",
            width=70,
            command=lambda d=session_dir: self._load_history(d),
        ).pack(pady=2)
        ctk.CTkButton(
            btn_col,
            text="コピー",
            width=70,
            command=lambda d=session_dir: self._copy_history(d),
        ).pack(pady=2)
        ctk.CTkButton(
            btn_col,
            text="削除",
            width=70,
            fg_color="#a93226",
            hover_color="#7b241c",
            command=lambda d=session_dir: self._delete_session(d),
        ).pack(pady=2)

    def _load_history(self, session_dir: Path) -> None:
        formatted = session_dir / "formatted.txt"
        raw = session_dir / "raw.txt"
        self._set_result_text(formatted.read_text(encoding="utf-8") if formatted.exists() else "")
        self._set_raw_text(raw.read_text(encoding="utf-8") if raw.exists() else "")
        self.tabs.set("録音")

    def _copy_history(self, session_dir: Path) -> None:
        formatted = session_dir / "formatted.txt"
        if formatted.exists():
            clipboard.copy(formatted.read_text(encoding="utf-8"))
            self._set_status("履歴をコピーしました", "green")

    def _delete_session(self, session_dir: Path) -> None:
        if not session_dir.exists():
            self._refresh_history()
            return
        if not messagebox.askyesno(
            "削除確認",
            f"このセッションを削除しますか？\n\n{session_dir.name}\n\n"
            "音声ファイル・生文字起こし・整形結果がすべて消えます。",
            parent=self,
        ):
            return
        try:
            shutil.rmtree(session_dir)
        except OSError as e:
            log.exception("Failed to delete session: %s", e)
            messagebox.showerror("削除失敗", f"削除できませんでした:\n{e}", parent=self)
            return
        self._set_status("セッションを削除しました", "green")
        self._refresh_history()

    def _delete_all_sessions(self) -> None:
        if not cfg.SESSIONS_DIR.exists():
            return
        sessions_dirs = [p for p in cfg.SESSIONS_DIR.iterdir() if p.is_dir()]
        if not sessions_dirs:
            self._set_status("削除する履歴がありません", "gray")
            return
        if not messagebox.askyesno(
            "全削除確認",
            f"履歴 {len(sessions_dirs)} 件をすべて削除しますか？\n\n"
            "この操作は取り消せません。",
            parent=self,
        ):
            return
        # Double-confirm for safety.
        if not messagebox.askyesno(
            "本当に削除しますか？",
            f"本当に {len(sessions_dirs)} 件すべて削除します。よろしいですか？",
            parent=self,
        ):
            return
        errors = 0
        for d in sessions_dirs:
            try:
                shutil.rmtree(d)
            except OSError as e:
                log.exception("Failed to delete %s: %s", d, e)
                errors += 1
        if errors:
            messagebox.showwarning(
                "一部削除失敗",
                f"{len(sessions_dirs) - errors} 件削除、{errors} 件は失敗しました。",
                parent=self,
            )
        else:
            self._set_status(f"{len(sessions_dirs)}件すべて削除しました", "green")
        self._refresh_history()

    # ---- Settings tab -------------------------------------------------

    def _build_settings_tab(self, parent: ctk.CTkFrame) -> None:
        scroll = ctk.CTkScrollableFrame(parent)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # API keys
        ctk.CTkLabel(
            scroll, text="APIキー", font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
        ).pack(fill="x", padx=8, pady=(8, 4))

        self.groq_entry = self._build_secret_entry(
            scroll,
            label="Groq API key",
            current=cfg.get_groq_key(),
        )
        self.gemini_entry = self._build_secret_entry(
            scroll,
            label="Gemini API key",
            current=cfg.get_gemini_key(),
        )

        save_keys_btn = ctk.CTkButton(scroll, text="APIキーを保存", command=self._save_api_keys)
        save_keys_btn.pack(anchor="w", padx=8, pady=(4, 12))

        # Hotkey
        ctk.CTkLabel(
            scroll,
            text="ホットキー（pynput形式）",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 4))

        ctk.CTkLabel(
            scroll,
            text="例: <cmd>+<shift>+<space>  /  <ctrl>+<shift>+<space>",
            text_color="gray",
            anchor="w",
        ).pack(fill="x", padx=8)

        self.hotkey_var = ctk.StringVar(value=self.settings.hotkey)
        ctk.CTkEntry(scroll, textvariable=self.hotkey_var, width=320).pack(
            anchor="w", padx=8, pady=(2, 4)
        )

        ctk.CTkLabel(
            scroll,
            text=(
                "macOS: 初回はアクセシビリティ権限が必要です。\n"
                "Terminal / iTerm（このアプリを起動したターミナル）を追加してON。\n"
                "権限付与後はai-audioの再起動が必要。"
            ),
            text_color="gray",
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=8, pady=(2, 4))

        ctk.CTkButton(
            scroll,
            text="アクセシビリティ設定を開く",
            width=220,
            command=self._open_accessibility_settings,
        ).pack(anchor="w", padx=8, pady=(4, 8))

        # Mode default
        ctk.CTkLabel(
            scroll,
            text="デフォルトの整形モード",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(12, 4))
        self.default_mode_var = ctk.StringVar(value=MODE_LABELS[self.settings.format_mode])
        ctk.CTkOptionMenu(
            scroll,
            values=list(MODE_LABELS.values()),
            variable=self.default_mode_var,
            width=160,
        ).pack(anchor="w", padx=8)

        # Models
        ctk.CTkLabel(
            scroll,
            text="モデル",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(12, 4))

        self.groq_model_var = ctk.StringVar(value=self.settings.groq_model)
        ctk.CTkLabel(scroll, text="Groq Whisper モデル", anchor="w").pack(fill="x", padx=8)
        ctk.CTkEntry(scroll, textvariable=self.groq_model_var, width=320).pack(
            anchor="w", padx=8, pady=(0, 4)
        )

        self.gemini_model_var = ctk.StringVar(value=self.settings.gemini_model)
        ctk.CTkLabel(scroll, text="Gemini モデル", anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkEntry(scroll, textvariable=self.gemini_model_var, width=320).pack(
            anchor="w", padx=8, pady=(0, 4)
        )

        # Custom instructions (style guide passed to Gemini formatter)
        ctk.CTkLabel(
            scroll,
            text="カスタムスタイル指示（Geminiの整形プロンプトに追加）",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(16, 4))

        ctk.CTkLabel(
            scroll,
            text=(
                "整形時の言い換え・スタイル指示を自由記述で追加できます。\n"
                "例: 苦手な発音の回避、避けたい語尾、好む言い回しなど。\n"
                "基本ルール（意味・数値・固有名詞は変えない）には従います。"
            ),
            text_color="gray",
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=8)

        self.use_custom_var = ctk.BooleanVar(value=self.settings.use_custom_instructions)
        ctk.CTkSwitch(
            scroll,
            text="カスタム指示を有効化",
            variable=self.use_custom_var,
        ).pack(anchor="w", padx=8, pady=(4, 4))

        self.custom_instructions_text = ctk.CTkTextbox(scroll, height=140, wrap="word")
        self.custom_instructions_text.pack(fill="x", padx=8, pady=(2, 4))
        self.custom_instructions_text.insert("1.0", self.settings.custom_instructions)

        ctk.CTkButton(
            scroll,
            text="デフォルトに戻す",
            width=140,
            fg_color="gray40",
            hover_color="gray30",
            command=self._reset_custom_instructions,
        ).pack(anchor="w", padx=8, pady=(0, 12))

        # Toggles
        self.save_sessions_var = ctk.BooleanVar(value=self.settings.save_sessions)
        ctk.CTkSwitch(
            scroll, text="セッションをローカル保存する", variable=self.save_sessions_var
        ).pack(anchor="w", padx=8, pady=(12, 4))

        self.notify_var = ctk.BooleanVar(value=self.settings.notify_on_complete)
        ctk.CTkSwitch(scroll, text="完了時にデスクトップ通知を出す", variable=self.notify_var).pack(
            anchor="w", padx=8, pady=(2, 12)
        )

        # Save settings
        save_settings_btn = ctk.CTkButton(scroll, text="設定を保存", command=self._save_settings)
        save_settings_btn.pack(anchor="w", padx=8, pady=(4, 12))

        self.settings_status = ctk.CTkLabel(scroll, text="", text_color="green")
        self.settings_status.pack(anchor="w", padx=8)

    def _build_secret_entry(
        self, parent: ctk.CTkFrame, label: str, current: str | None
    ) -> ctk.CTkEntry:
        ctk.CTkLabel(parent, text=label, anchor="w").pack(fill="x", padx=8, pady=(8, 0))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8)
        var = ctk.StringVar(value=current or "")
        entry = ctk.CTkEntry(row, textvariable=var, show="*", width=380)
        entry.pack(side="left")

        show_var = ctk.BooleanVar(value=False)

        def toggle_show() -> None:
            entry.configure(show="" if show_var.get() else "*")

        ctk.CTkCheckBox(row, text="表示", variable=show_var, command=toggle_show, width=80).pack(
            side="left", padx=8
        )
        return entry

    def _save_api_keys(self) -> None:
        groq_value = self.groq_entry.get().strip()
        gemini_value = self.gemini_entry.get().strip()
        if groq_value:
            cfg.set_groq_key(groq_value)
        if gemini_value:
            cfg.set_gemini_key(gemini_value)
        self.settings_status.configure(text="APIキーを保存しました", text_color="green")
        self._fade_settings_status()

    def _save_settings(self) -> None:
        old_hotkey = self.settings.hotkey
        self.settings.hotkey = self.hotkey_var.get().strip() or self.settings.hotkey
        self.settings.format_mode = MODE_REVERSE[self.default_mode_var.get()]
        self.settings.groq_model = self.groq_model_var.get().strip() or self.settings.groq_model
        self.settings.gemini_model = (
            self.gemini_model_var.get().strip() or self.settings.gemini_model
        )
        self.settings.save_sessions = self.save_sessions_var.get()
        self.settings.notify_on_complete = self.notify_var.get()
        self.settings.use_custom_instructions = self.use_custom_var.get()
        self.settings.custom_instructions = self.custom_instructions_text.get(
            "1.0", "end"
        ).rstrip("\n")
        cfg.save_settings(self.settings)
        # Reflect mode change in the recording-tab dropdown.
        self.mode_var.set(MODE_LABELS[self.settings.format_mode])
        # Re-register the hotkey if it changed.
        if self.settings.hotkey != old_hotkey:
            self._restart_hotkey_listener()
        self.settings_status.configure(text="設定を保存しました", text_color="green")
        self._fade_settings_status()

    def _open_accessibility_settings(self) -> None:
        macos_helpers.open_accessibility_settings()
        if macos_helpers.is_macos() and not macos_helpers.is_accessibility_trusted():
            self.settings_status.configure(
                text="権限付与後、ai-audioを再起動してね",
                text_color="orange",
            )
            self._fade_settings_status()

    def _reset_custom_instructions(self) -> None:
        if not messagebox.askyesno(
            "カスタム指示をデフォルトに戻す",
            "編集中のカスタム指示を破棄して、デフォルトに戻しますか？",
            parent=self,
        ):
            return
        self.custom_instructions_text.delete("1.0", "end")
        self.custom_instructions_text.insert("1.0", cfg.DEFAULT_CUSTOM_INSTRUCTIONS)

    def _fade_settings_status(self) -> None:
        self.after(2500, lambda: self.settings_status.configure(text=""))

    # ---- Recording flow ----------------------------------------------

    def toggle_recording(self) -> None:
        """Toggle recording state. Safe to call from any thread via after()."""
        if self._is_processing:
            return
        if self.recorder.is_recording:
            self._stop_and_process()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        if not cfg.get_groq_key() or not cfg.get_gemini_key():
            self._set_status("APIキー未設定: 設定タブで登録してね", "red")
            self.tabs.set("設定")
            return
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to start recording: %s", e)
            self._set_status(f"録音開始失敗: {e}", "red")
            return

        self._record_started_at = _now_seconds()
        self.record_btn.configure(text="■ 停止", fg_color="#27ae60", hover_color="#1e8449")
        self._set_status("録音中…", "red")
        self._tick_timer()

        # Minimize the main window to the Dock first, then create the compact
        # recording window after a tiny delay. On macOS, creating an
        # overrideredirect+topmost child Toplevel right before changing the
        # parent's window state causes Aqua window-ordering glitches; sequencing
        # iconify -> event-loop tick -> child creation is the safe order.
        self.iconify()
        self.after(50, self._show_compact_window)

    def _stop_and_process(self) -> None:
        print("[ai-audio] stop: entry", flush=True)
        try:
            result = self.recorder.stop()
            print(
                f"[ai-audio] stop: recorder.stop OK ({result.duration_seconds:.1f}s)",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to stop recording: %s", e)
            print(f"[ai-audio] stop: recorder.stop FAILED: {e}", flush=True)
            self._set_status(f"録音停止失敗: {e}", "red")
            self._stop_level_polling()
            self._close_compact_window()
            self.show_window()
            self._refresh_status_idle()
            return

        self._stop_timer()
        self._stop_level_polling()
        self._close_compact_window()
        self.show_window()
        print("[ai-audio] stop: UI restored, launching pipeline thread", flush=True)

        self.record_btn.configure(state="disabled", text="処理中…", fg_color="gray50")
        self._set_status(f"処理中… ({result.duration_seconds:.1f}秒分を文字起こし中)", "orange")
        self._is_processing = True

        mode = MODE_REVERSE[self.mode_var.get()]
        thread = threading.Thread(
            target=self._run_pipeline_worker, args=(result, mode), daemon=True
        )
        thread.start()

    def _pump_ui_queue(self) -> None:
        """Drain pending cross-thread UI events on the Tk main thread."""
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    log.exception("UI event handler raised")
        except queue.Empty:
            pass
        # Reschedule on the main window — this loop must keep running for the
        # lifetime of the app.
        self.after(50, self._pump_ui_queue)

    def _post_to_ui(self, fn: Callable[[], None]) -> None:
        """Thread-safe scheduler: enqueue a callable for the Tk main thread."""
        self._ui_queue.put(fn)

    def _run_pipeline_worker(self, recording: RecordingResult, mode: FormatMode) -> None:
        print("[ai-audio] pipeline worker: started", flush=True)
        try:
            result = controller.process(recording, self.settings, mode=mode)
            print(
                f"[ai-audio] pipeline worker: success "
                f"({len(result.formatted_text)} chars, fallback={result.used_fallback})",
                flush=True,
            )
            self._post_to_ui(lambda r=result: self._on_pipeline_success(r))
        except Exception as e:  # noqa: BLE001
            log.exception("Pipeline failed: %s", e)
            print(f"[ai-audio] pipeline worker: ERROR {e}", flush=True)
            err = str(e)
            self._post_to_ui(lambda m=err: self._on_pipeline_error(m))

    def _on_pipeline_success(self, result: controller.PipelineResult) -> None:
        self._last_result = result
        self._set_result_text(result.formatted_text)
        self._set_raw_text(result.raw_text)
        if result.used_fallback:
            self._set_status("整形失敗 → 生テキストをコピー（履歴に保存済み）", "orange")
        else:
            self._set_status(
                f"完了 / {len(result.formatted_text)}文字 / {result.duration_seconds:.1f}秒",
                "green",
            )
        self._reset_record_button()
        self._is_processing = False
        self._refresh_history()

    def _on_pipeline_error(self, message: str) -> None:
        self._set_status(f"エラー: {message}", "red")
        self._reset_record_button()
        self._is_processing = False

    def _reset_record_button(self) -> None:
        self.record_btn.configure(
            state="normal",
            text="● 録音開始",
            fg_color="#c0392b",
            hover_color="#a93226",
        )

    # ---- Result-area helpers -----------------------------------------

    def _set_result_text(self, text: str) -> None:
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)

    def _set_raw_text(self, text: str) -> None:
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", text)

    def _copy_result(self) -> None:
        text = self.result_text.get("1.0", "end").rstrip("\n")
        if text:
            clipboard.copy(text)
            self._set_status("クリップボードへコピーしました", "green")

    def _copy_edited(self) -> None:
        text = self.result_text.get("1.0", "end").rstrip("\n")
        if not text:
            return
        clipboard.copy(text)
        # Persist edits back to the latest session file if available.
        if self._last_result is not None:
            try:
                (self._last_result.session_root / "formatted.txt").write_text(
                    text, encoding="utf-8"
                )
            except OSError:
                log.warning("Could not persist edited transcript to session file.")
        self._set_status("編集後をコピーしました", "green")
        self._refresh_history()

    def _reformat_last(self) -> None:
        if self._is_processing:
            return
        raw = self.raw_text.get("1.0", "end").rstrip("\n")
        if not raw.strip():
            self._set_status("再整形対象の生文字起こしがありません", "orange")
            return
        self._is_processing = True
        self.record_btn.configure(state="disabled")
        self._set_status("再整形中…", "orange")

        mode = MODE_REVERSE[self.mode_var.get()]
        thread = threading.Thread(target=self._run_reformat_worker, args=(raw, mode), daemon=True)
        thread.start()

    def _run_reformat_worker(self, raw_text: str, mode: FormatMode) -> None:
        from ai_audio.llm.gemini_client import GeminiFormatter

        try:
            gemini_key = cfg.get_gemini_key()
            if not gemini_key:
                raise controller.MissingApiKeyError("Gemini API key not set")
            formatter = GeminiFormatter(
                api_key=gemini_key,
                model=self.settings.gemini_model,
                fallback_model=self.settings.gemini_fallback_model,
            )
            formatted = formatter.format(raw_text, mode=mode)
            self._post_to_ui(lambda t=formatted: self._on_reformat_done(t, None))
        except Exception as e:  # noqa: BLE001
            log.exception("Reformat failed: %s", e)
            err = str(e)
            self._post_to_ui(lambda m=err: self._on_reformat_done(raw_text, m))

    def _on_reformat_done(self, text: str, error: str | None) -> None:
        self._set_result_text(text)
        if error:
            self._set_status(f"再整形失敗（生テキスト表示）: {error}", "orange")
        else:
            clipboard.copy(text)
            self._set_status("再整形完了 → クリップボードへコピー", "green")
        self.record_btn.configure(state="normal")
        self._is_processing = False

    def _clear_result(self) -> None:
        self._set_result_text("")
        self._set_raw_text("")
        self._refresh_status_idle()

    # ---- Status / timer ----------------------------------------------

    def _set_status(self, text: str, color: str | None = None) -> None:
        self.status_label.configure(text=text)
        if color:
            self.status_label.configure(text_color=color)

    def _refresh_status_idle(self) -> None:
        self._set_status("待機中", "gray")
        self.timer_label.configure(text="00:00")

    def _tick_timer(self) -> None:
        if not self.recorder.is_recording or self._record_started_at is None:
            return
        elapsed = int(_now_seconds() - self._record_started_at)
        mm, ss = divmod(elapsed, 60)
        self.timer_label.configure(text=f"{mm:02d}:{ss:02d}")
        self._timer_job = self.after(500, self._tick_timer)

    def _stop_timer(self) -> None:
        if self._timer_job:
            self.after_cancel(self._timer_job)
            self._timer_job = None

    # ---- Compact recording window -----------------------------------

    def _show_compact_window(self) -> None:
        if self._compact_window is not None:
            return
        self._compact_window = CompactWindow(
            self,
            on_stop=self._on_compact_stop,
            initial_geometry=self.settings.compact_window_geometry,
        )
        self._poll_level()

    def _on_compact_stop(self) -> None:
        # Called from inside the compact window's stop-button event. We CANNOT
        # destroy the compact window synchronously from here — Tk on macOS
        # corrupts its internal state when a widget is destroyed from within
        # its own event callback, which manifests as the post-stop pipeline
        # silently freezing (Tk main loop stops dispatching after() callbacks).
        # Defer the toggle to the next event-loop tick so we return from the
        # button callback first.
        self.after(0, self.toggle_recording)

    def _close_compact_window(self) -> None:
        if self._compact_window is None:
            return
        win = self._compact_window
        # Detach the reference IMMEDIATELY so subsequent _poll_level / status
        # callbacks early-return instead of touching a dying widget.
        self._compact_window = None
        # Remember geometry for next time.
        try:
            geo = win.get_geometry_string()
            if geo:
                self.settings.compact_window_geometry = geo
                cfg.save_settings(self.settings)
        except Exception:
            pass
        # Defer the actual destroy() to the next event-loop tick — destroying
        # a Toplevel inline can deadlock Tk on macOS when this is called from
        # within an event still being dispatched on that window.
        try:
            self.after(0, win.close)
        except Exception:
            try:
                win.close()
            except Exception:
                pass

    def _poll_level(self) -> None:
        if self._compact_window is None or not self.recorder.is_recording:
            self._level_poll_job = None
            return
        level = self.recorder.current_level
        self._compact_window.update_level(level)
        if self._record_started_at is not None:
            elapsed = int(_now_seconds() - self._record_started_at)
            self._compact_window.update_timer(elapsed)
        # Force a redraw of the compact window's widgets — overrideredirect
        # Toplevels on macOS don't flush idle tasks reliably on their own.
        try:
            self._compact_window.update_idletasks()
        except Exception:
            pass
        # IMPORTANT: schedule on MainWindow.after(), NOT compact_window.after().
        # On macOS, after() callbacks anchored to an overrideredirect+topmost
        # CTkToplevel do not fire reliably, even though the master CTk's
        # after() callbacks fire just fine. So we use MainWindow as the timer
        # anchor for this polling loop.
        self._level_poll_job = self.after(60, self._poll_level)

    def _stop_level_polling(self) -> None:
        if self._level_poll_job:
            try:
                self.after_cancel(self._level_poll_job)
            except Exception:
                pass
        self._level_poll_job = None

    # ---- Window lifecycle --------------------------------------------

    def _on_close(self) -> None:
        # Step 5 will hide-to-tray here. For now, fully exit so users can
        # close the window normally during dev.
        try:
            self._hotkey_listener.stop()
        except Exception:
            pass
        self.quit()
        self.destroy()

    # ---- Hotkey listener ---------------------------------------------

    def _start_hotkey_listener(self) -> None:
        """Register the global hotkey from settings."""
        combo = self.settings.hotkey
        ok = self._hotkey_listener.start(combo, self._on_hotkey_pressed)
        if ok:
            print(f"[ai-audio] hotkey registered: {combo}", flush=True)
        else:
            print(
                f"[ai-audio] HOTKEY FAILED to register {combo!r}: "
                f"{self._hotkey_listener.last_error}\n"
                "  -> On macOS, grant Accessibility permission to your terminal:\n"
                "     System Settings -> Privacy & Security -> Accessibility -> "
                "add Terminal/iTerm -> restart this app.",
                flush=True,
            )
            log.warning(
                "Hotkey '%s' could not be registered: %s",
                combo,
                self._hotkey_listener.last_error,
            )

    def _restart_hotkey_listener(self) -> None:
        self._hotkey_listener.stop()
        self._start_hotkey_listener()

    def _check_accessibility(self) -> None:
        """On macOS, check Accessibility permission and guide the user."""
        if not macos_helpers.is_macos():
            return
        if macos_helpers.is_accessibility_trusted():
            print("[ai-audio] accessibility permission: GRANTED", flush=True)
            return

        print(
            "[ai-audio] accessibility permission: NOT granted — hotkey will not fire",
            flush=True,
        )
        # Trigger Apple's standard prompt — sometimes it auto-registers the
        # process in System Settings, sometimes it doesn't (especially under
        # `uv run`). Either way, also show our own dialog with copy/open
        # buttons so the user can add the binary manually.
        macos_helpers.request_accessibility_with_prompt()
        self._show_accessibility_help_dialog()

    def _show_accessibility_help_dialog(self) -> None:
        """Custom dialog explaining how to grant Accessibility manually."""
        terminal = macos_helpers.current_terminal_name()
        python_path = macos_helpers.current_python_path()

        dialog = ctk.CTkToplevel(self)
        dialog.title("アクセシビリティ権限が必要")
        dialog.geometry("620x440")
        dialog.transient(self)
        dialog.attributes("-topmost", True)

        wrap = 580

        ctk.CTkLabel(
            dialog,
            text="ホットキー（Cmd+Shift+Space）を効かせるには",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(18, 4))

        ctk.CTkLabel(
            dialog,
            text="macOSの「アクセシビリティ」権限が必要。リストが空なら手動追加してね。",
            wraplength=wrap,
            anchor="w",
            justify="left",
            text_color="gray",
        ).pack(fill="x", padx=20, pady=(0, 12))

        ctk.CTkLabel(
            dialog,
            text="手順:",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 4))

        ctk.CTkLabel(
            dialog,
            text=(
                "1. 下の「システム設定を開く」ボタンを押す\n"
                "2. 左下の「+」をクリック → ファイル選択ダイアログが出る\n"
                "3. 以下のいずれかを追加（どちらでもOK、Aの方が簡単）:\n"
                f"   A) ターミナル: 「アプリケーション」から {terminal} を選ぶ\n"
                "   B) Pythonバイナリ: ↓のパスをコピーして、Cmd+Shift+G で\n"
                "      ファイルダイアログに貼り付け\n"
                "4. リストでON にする\n"
                "5. ai-audio を完全終了 → 再起動"
            ),
            wraplength=wrap,
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(
            dialog,
            text="Pythonバイナリのパス:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(8, 2))

        path_box = ctk.CTkEntry(dialog, width=580)
        path_box.pack(fill="x", padx=20, pady=(0, 10))
        path_box.insert(0, python_path)
        path_box.configure(state="readonly")

        btns = ctk.CTkFrame(dialog, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(4, 16))

        def copy_path() -> None:
            clipboard.copy(python_path)
            self._set_status("Pythonパスをコピーしました", "green")

        def open_settings() -> None:
            macos_helpers.open_accessibility_settings()

        def reveal_python() -> None:
            macos_helpers.reveal_in_finder(python_path)

        ctk.CTkButton(btns, text="システム設定を開く", width=160, command=open_settings).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btns, text="パスをコピー", width=130, command=copy_path).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btns, text="Finderで表示", width=130, command=reveal_python).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            btns,
            text="閉じる",
            width=80,
            fg_color="gray40",
            hover_color="gray30",
            command=dialog.destroy,
        ).pack(side="right", padx=4)

    def _on_hotkey_pressed(self) -> None:
        """Called from the pynput listener thread.

        We must NOT touch Tk widgets directly here — schedule the actual
        toggle on the Tk main thread via ``after(0, ...)``.
        """
        print(f"[ai-audio] hotkey fired (recording={self.recorder.is_recording})", flush=True)
        try:
            self.after(0, self._handle_hotkey)
        except Exception:
            log.exception("Failed to dispatch hotkey toggle to UI thread")

    def _handle_hotkey(self) -> None:
        """Runs on the Tk main thread when the global hotkey fires.

        Behavior:
        - If ai-audio is in the background, force it to the foreground first.
        - Then toggle recording (start if idle, stop+process if recording).
        """
        try:
            _bring_app_to_front()
        except Exception:
            log.exception("Failed to bring app to front")
        # Make sure the user can see SOMETHING happened, even if recording
        # toggling is blocked by the processing-in-progress flag.
        if not self.recorder.is_recording:
            # Re-show main window if it was iconified/hidden, so the user has
            # visible confirmation of the activation when they press the hotkey
            # before any recording session.
            try:
                self.show_window()
            except Exception:
                pass
        self.toggle_recording()

    def show_window(self) -> None:
        # Works for both withdrawn and iconified states.
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide_window(self) -> None:
        # Use iconify (Dock minimize) instead of withdraw on macOS — withdraw
        # is unreliable when a child Toplevel was just created, and iconify
        # gives the user a visible Dock affordance to bring the app back.
        self.iconify()


def _now_seconds() -> float:
    import time

    return time.time()


def _bring_app_to_front() -> None:
    """Force this Python process to become the foreground app on macOS.

    Tk's lift() / focus_force() are unreliable when another app has focus on
    macOS. Using NSApp.activateIgnoringOtherApps_(True) is the documented
    Cocoa way to grab focus from anywhere — and pyobjc is already pulled in
    transitively as a dependency on macOS.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        log.debug("NSApp activation unavailable; falling back to no-op", exc_info=True)


def run() -> None:
    """Launch the GUI."""
    app = MainWindow()
    app.mainloop()
