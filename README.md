# ai-audio

Voice-input transcription tool that mimics 勝間和代-style workflow:

```
[Hotkey toggle] -> Mic recording -> Groq Whisper (whisper-large-v3-turbo)
              -> Gemini 3.1 Flash-Lite (formatting) -> Clipboard
```

Designed for drafting meeting scripts via voice instead of typing, then
later as a general-purpose voice input pipe to other AI tools.

## Features

- Toggle hotkey (default: `Cmd+Shift+Space` on macOS, `Ctrl+Shift+Space` on Windows)
- No push-to-talk (stutter-friendly: long pauses do not stop recording)
- Three formatting modes: `default` / `ai_input` / `summary`
- Tray icon for status and mode switching
- Auto-chunks recordings longer than 12 minutes (Groq 25 MB upload limit)
- Local session backup (raw transcript + formatted text + audio)
- API keys stored in OS keyring (not plain text)

## Setup

```bash
# 1. Install dependencies
uv sync --all-extras

# 2. Configure API keys
uv run ai-audio config set-key groq
uv run ai-audio config set-key gemini

# 3. Run a one-shot recording test (CLI)
uv run ai-audio record

# 4. Start the tray app
uv run ai-audio tray
```

## Required API Keys

- **Groq**: <https://console.groq.com/keys> (free tier is enough for personal use)
- **Gemini**: <https://aistudio.google.com/apikey>

## Project Structure

```
src/ai_audio/
  __main__.py        # CLI entry point (typer)
  config.py          # keyring + toml settings
  controller.py      # state machine
  audio/             # mic recorder, chunker
  hotkey/            # global hotkey listener
  stt/               # Groq Whisper client
  llm/               # Gemini formatter
  desktop/           # tray, clipboard, notifications
  storage/           # session persistence
```

## Development

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```
