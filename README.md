# ai-audio

勝間和代スタイルのワークフローを再現する、音声入力・文字起こしツール。

```
[ホットキーでトグル] -> マイク録音 -> Groq Whisper (whisper-large-v3-turbo)
              -> Gemini 3.1 Flash-Lite (整形) -> クリップボード
```

タイピングの代わりに音声で議事録や台本のドラフトを作ることを想定して設計。
将来的には他のAIツールへの汎用的な音声入力パイプとしても使えます。

## 機能

- トグル式ホットキー（デフォルト: macOSは `Cmd+Shift+Space` / Windowsは `Ctrl+Shift+Space`）
- プッシュ・トゥ・トークではない（長い沈黙でも録音が止まらず、どもりにやさしい）
- 3つの整形モード: `default` / `ai_input` / `summary`
- ステータス表示・モード切り替え用のトレイアイコン
- 12分を超える録音は自動でチャンク分割（Groqの25MBアップロード上限に対応）
- ローカルへのセッションバックアップ（生の文字起こし + 整形済みテキスト + 音声）
- APIキーはOSのキーリングに保存（平文では保持しない）

## セットアップ

```bash
# 1. 依存関係のインストール
uv sync --all-extras

# 2. APIキーの設定
uv run ai-audio config set-key groq
uv run ai-audio config set-key gemini

# 3. ワンショット録音テスト（CLI）
uv run ai-audio record

# 4. トレイアプリの起動
uv run ai-audio tray
```

## 必要なAPIキー

- **Groq**: <https://console.groq.com/keys>（個人利用なら無料枠で十分）
- **Gemini**: <https://aistudio.google.com/apikey>

## プロジェクト構成

```
src/ai_audio/
  __main__.py        # CLIエントリポイント (typer)
  config.py          # keyring + toml 設定
  controller.py      # ステートマシン
  audio/             # マイク録音・チャンク分割
  hotkey/            # グローバルホットキーのリスナー
  stt/               # Groq Whisper クライアント
  llm/               # Gemini 整形処理
  desktop/           # トレイ・クリップボード・通知
  storage/           # セッションの永続化
```

## 開発

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```
