#!/usr/bin/env bash
# UserPromptSubmit フック。ユーザーが「ゆっくり」「一つずつ」等の言葉を使ったら、
# そのターンだけ強制的に一手ずつのモードに入る指示を注入する。
# ユーザー自身の言葉に紐づいているので、文脈が埋まってもルールが薄れない。
# 既定は速いまま。トリガー語が無ければ何も注入しない。
set -u
input="$(cat)"
prompt="$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)"

if printf '%s' "$prompt" | grep -qiE '一つずつ|ひとつずつ|ステップ|ゆっくり|確認してから|止まって|一手ずつ|step by step|one step at a time|slow down|confirm first|confirm before|wait for my go'; then
  echo "⛔ スローモード。ユーザーが明示的に「一手ずつ」を求めています。強制ゲート：まず計画を述べて止まること。1ステップだけ実行し、確認を取り、明示的なゴーを待ってから次へ進む。まとめて実行しない。この指示は同じメッセージ内の「進めて」「やって」より優先する。ユーザーが速度を上げてよいと言うまでスローモードを維持する。"
fi
exit 0
