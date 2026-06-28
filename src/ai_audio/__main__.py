"""CLI entry point.

Subcommands::

    ai-audio config show
    ai-audio config set-key {groq|gemini}
    ai-audio config set-hotkey "<cmd>+<shift>+<space>"
    ai-audio record [--mode default|ai_input|summary]
    ai-audio tray   # (Step 5)
"""

from __future__ import annotations

import getpass
import sys
import time
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

from ai_audio import config as cfg
from ai_audio import controller
from ai_audio.audio.recorder import Recorder
from ai_audio.llm.prompts import FormatMode

app = typer.Typer(help="Voice-input transcription tool (Groq Whisper + Gemini Flash-Lite)")
config_app = typer.Typer(help="Manage API keys and settings")
app.add_typer(config_app, name="config")

console = Console()


@config_app.command("show")
def config_show() -> None:
    """Show current settings (keys are masked)."""
    settings = cfg.load_settings()
    groq = cfg.get_groq_key()
    gemini = cfg.get_gemini_key()
    console.print(
        Panel.fit(
            f"hotkey:           {settings.hotkey}\n"
            f"sample_rate:      {settings.sample_rate}\n"
            f"channels:         {settings.channels}\n"
            f"chunk_seconds:    {settings.chunk_seconds}\n"
            f"format_mode:      {settings.format_mode}\n"
            f"groq_model:       {settings.groq_model}\n"
            f"gemini_model:     {settings.gemini_model}\n"
            f"groq_api_key:     {_mask(groq)}\n"
            f"gemini_api_key:   {_mask(gemini)}\n"
            f"config path:      {cfg.CONFIG_PATH}",
            title="ai-audio settings",
        )
    )


@config_app.command("set-key")
def config_set_key(
    provider: Annotated[str, typer.Argument(help="'groq' or 'gemini'")],
) -> None:
    """Store an API key in the OS keyring."""
    provider = provider.lower()
    if provider not in {"groq", "gemini"}:
        console.print("[red]provider must be 'groq' or 'gemini'[/red]")
        raise typer.Exit(code=1)
    value = getpass.getpass(f"{provider} API key (input hidden): ").strip()
    if not value:
        console.print("[red]empty key — aborted[/red]")
        raise typer.Exit(code=1)
    if provider == "groq":
        cfg.set_groq_key(value)
    else:
        cfg.set_gemini_key(value)
    console.print(f"[green]Saved {provider} API key to OS keyring.[/green]")


@config_app.command("delete-key")
def config_delete_key(
    provider: Annotated[str, typer.Argument(help="'groq' or 'gemini'")],
) -> None:
    provider = provider.lower()
    if provider == "groq":
        cfg.delete_groq_key()
    elif provider == "gemini":
        cfg.delete_gemini_key()
    else:
        console.print("[red]provider must be 'groq' or 'gemini'[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted {provider} API key.[/green]")


@config_app.command("set-hotkey")
def config_set_hotkey(hotkey: str) -> None:
    """Update the global hotkey string (pynput format)."""
    settings = cfg.load_settings()
    settings.hotkey = hotkey
    cfg.save_settings(settings)
    console.print(f"[green]Hotkey set to {hotkey}[/green]")


@config_app.command("set-mode")
def config_set_mode(
    mode: Annotated[str, typer.Argument(help="'script' or 'ai_input'")],
) -> None:
    if mode not in {"script", "ai_input"}:
        console.print("[red]mode must be 'script' or 'ai_input'[/red]")
        raise typer.Exit(code=1)
    settings = cfg.load_settings()
    settings.format_mode = mode  # type: ignore[assignment]
    cfg.save_settings(settings)
    console.print(f"[green]Default format mode set to {mode}[/green]")


@app.command()
def record(
    mode: Annotated[
        str | None,
        typer.Option(help="script | ai_input (overrides config)"),
    ] = None,
) -> None:
    """One-shot CLI recording test. Press Enter to start, Enter again to stop."""
    settings = cfg.load_settings()
    chosen_mode: FormatMode | None = None
    if mode:
        if mode not in {"script", "ai_input"}:
            console.print("[red]mode must be 'script' or 'ai_input'[/red]")
            raise typer.Exit(code=1)
        chosen_mode = mode  # type: ignore[assignment]

    if not cfg.get_groq_key():
        console.print("[red]Groq key missing.[/red] Run: ai-audio config set-key groq")
        raise typer.Exit(code=1)
    if not cfg.get_gemini_key():
        console.print("[red]Gemini key missing.[/red] Run: ai-audio config set-key gemini")
        raise typer.Exit(code=1)

    recorder = Recorder(sample_rate=settings.sample_rate, channels=settings.channels)

    console.print("[cyan]Press Enter to START recording...[/cyan]")
    input()
    recorder.start()
    started_at = time.time()
    console.print("[bold red]● Recording...[/bold red] Press Enter to STOP.")
    input()
    result = recorder.stop()
    elapsed = time.time() - started_at
    console.print(
        f"[green]Stopped.[/green] {result.duration_seconds:.1f}s captured ({elapsed:.1f}s wall)"
    )

    console.print("[cyan]Transcribing & formatting...[/cyan]")
    pipeline_result = controller.process(result, settings, mode=chosen_mode)

    console.print(
        Panel.fit(
            pipeline_result.formatted_text or "(empty)",
            title="Formatted (copied to clipboard)",
            border_style="green" if not pipeline_result.used_fallback else "yellow",
        )
    )
    console.print(f"[dim]Session saved to: {pipeline_result.session_root}[/dim]")
    if pipeline_result.used_fallback:
        console.print(
            "[yellow]Note: Gemini formatting failed. Raw transcript was copied instead.[/yellow]"
        )


@app.command()
def gui() -> None:
    """Launch the main GUI window (customtkinter)."""
    from ai_audio.gui.main_window import run as run_gui

    run_gui()


@app.command()
def tray() -> None:
    """Start the tray-resident app (implemented in Step 5)."""
    console.print("[yellow]Tray mode is not yet implemented (coming in Step 5).[/yellow]")
    sys.exit(0)


def _mask(value: str | None) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]} ({len(value)} chars)"


if __name__ == "__main__":
    app()
