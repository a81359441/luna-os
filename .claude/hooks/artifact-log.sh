#!/usr/bin/env bash
# PostToolUse(Artifact)。アーティファクトを公開するたびに .session/artifacts.jsonl へ記録し、
# ブリーフの「最近の成果物」からリンクできるようにする。ギャラリーはツールであって
# スクリプトから呼べる API ではないので、ブリーフがページの存在を知る手段はこのフックだけ。
# 何も止めない。
set -u
ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
mkdir -p "$ROOT/.session"
export AI_HOOK_EVENT="$(cat)"
python3 - "$ROOT/.session/artifacts.jsonl" <<'PY' 2>/dev/null || true
import datetime, json, os, re, sys
from pathlib import Path
ev = json.loads(os.environ["AI_HOOK_EVENT"])
inp = ev.get("tool_input") or {}
if inp.get("action", "publish") != "publish" or inp.get("asset"):
    sys.exit(0)
m = re.search(r"https://claude\.ai/(?:code/)?artifact/[\w-]+", json.dumps(ev.get("tool_response"), ensure_ascii=False))
if not m:
    sys.exit(0)
src = inp.get("file_path")
title = inp.get("title") or ""
if src:
    try:
        t = re.search(r"<title[^>]*>(.*?)</title>", Path(src).read_text(encoding="utf-8", errors="ignore")[:4000], re.S | re.I)
        title = re.sub(r"\s+", " ", t.group(1)).strip() if t else title
    except OSError:
        pass
row = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "title": title or Path(src or "artifact").stem,
       "url": m.group(0), "source": src}
with open(sys.argv[1], "a", encoding="utf-8") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
exit 0
