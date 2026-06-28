"""Prompt templates for the Gemini formatter.

Two modes:

- ``script`` (台本用): cleanup + heavy rewrite for use as a presentation /
  meeting script. Synonym substitution **is** allowed when guided by the
  user's custom instructions (e.g. "avoid words starting with あ行 because
  they trigger blocks"). Designed to produce text the user can later read
  aloud comfortably.
- ``ai_input`` (AI入力用): light cleanup only — fillers and stutter
  repetitions are removed, punctuation is added minimally, but the speaker's
  original wording is preserved as faithfully as possible. The user's custom
  instructions are deliberately **not** applied here, since the goal is to
  keep raw intent intact for the downstream AI.
"""

from __future__ import annotations

from typing import Literal

FormatMode = Literal["script", "ai_input"]


SYSTEM_PROMPT_SCRIPT = """\
あなたは日本語の音声文字起こしを「プレゼン・会議の台本」として清書する整形アシスタントです。

基本ルール（最優先）:
- 「えー」「あの」「うーん」「まあ」「その」などのフィラーは削除する
- 吃音による語頭反復（例：「き、き、今日は」→「今日は」）は意味を変えない範囲で1回に整理する
- 自然な句読点と段落改行を補う
- 話し言葉の語尾は読みやすい書き言葉へ整える（過度な敬語化はしない）
- 内容・主張・数値・固有名詞・専門用語は絶対に変えない、追加しない、要約しない
- 元音声に存在しない情報は決して挿入しない

語彙選択について:
- 後で発話者が読み上げる前提なので、読みやすさを優先する
- 同義の言い換え（意味・ニュアンス・固有名詞・数値を保つ範囲）は、
  下の「ユーザー指示」がある場合に積極的に適用する
- ユーザー指示と「基本ルール」が衝突する場合は、基本ルールを優先し、原文のまま残す
"""


SYSTEM_PROMPT_AI_INPUT = """\
あなたは日本語の音声文字起こしを「AIチャットへの入力テキスト」として整形するアシスタントです。

基本ルール（最優先）:
- 「えー」「あの」「うーん」「まあ」などのフィラーは削除する
- 吃音による語頭反復は1回に整理する
- 自然な句読点と最低限の改行を補う
- それ以外の語彙・表現は **可能な限り原文のまま** 残す
- 言い換え・書き換え・要約は **行わない**
- 内容・主張・数値・固有名詞・口癖・語尾は絶対に変えない、追加しない
- 元音声に存在しない情報は決して挿入しない

このモードはユーザーが後段のAIに直接食わせるためのものです。
発話者の意図とニュアンスを最大限保つことを優先してください。
"""


OUTPUT_FOOTER = """\

出力ルール:
- 出力は整形済みテキストのみ。前置き・解説・タイトル・コードブロック等は付けない
"""


def _base_for(mode: FormatMode) -> str:
    if mode == "ai_input":
        return SYSTEM_PROMPT_AI_INPUT
    return SYSTEM_PROMPT_SCRIPT


def build_system_prompt(mode: FormatMode, custom_instructions: str | None = None) -> str:
    """Compose the final system prompt.

    The user's free-form ``custom_instructions`` (e.g. avoid words starting
    with certain phonemes) is injected for ``script`` mode only — ``ai_input``
    mode deliberately ignores it because the goal there is faithful raw
    transcription rather than rewriting.
    """
    base = _base_for(mode)
    instructions = (custom_instructions or "").strip()
    if not instructions or mode == "ai_input":
        return base + OUTPUT_FOOTER

    user_block = (
        "\nユーザー指示（基本ルールに反しない範囲で適用）:\n"
        f"{instructions}\n"
        "ただし、自然な言い換えがない場合、意味やニュアンスが変わる場合、"
        "固有名詞・数値・専門用語の場合は、原文のまま残してください。\n"
    )
    return base + user_block + OUTPUT_FOOTER


def system_prompt_for(mode: FormatMode) -> str:
    """Backward-compatible helper (no custom instructions)."""
    return build_system_prompt(mode)


def user_prompt(transcript: str) -> str:
    return f"以下の文字起こしを整形してください。\n\n---\n{transcript}\n---"
