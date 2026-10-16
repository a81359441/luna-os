#!/usr/bin/env bash
# SessionStart フック。標準出力は新しいセッションの文脈として注入される。
#
# closeout の「続ける」出口が `.session/resume-pending` に短い引き継ぎ
# （[~] の backlog 項目、ブランチ、次の一手）を書く。人間が /clear
# （または /compact、自動圧縮）を押したあと、それを一度だけ注入して消す。
# 何も打たずに話が続く。長く残る正本は git の中の [~] 項目で、この
# ファイルは /clear を越えるための一回きりの橋にすぎない。
# ポインタが無ければ、完全なクリアは完全なクリアのまま。
set -u
PENDING="${CLAUDE_PROJECT_DIR:-$PWD}/.session/resume-pending"

if [ -f "$PENDING" ]; then
  if [ -s "$PENDING" ]; then
    echo "▶ closeout の続きから再開します。一行でそう伝えてから、ここから続けてください:"
    echo
    cat "$PENDING"
  fi
  rm -f "$PENDING"
fi
exit 0
