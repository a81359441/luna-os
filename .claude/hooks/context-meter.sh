#!/usr/bin/env bash
# UserPromptSubmit フック。会話履歴から文脈の量を読み、帯ごとに一度だけ、
# `closeout` の「続ける」出口を提案するよう AI に伝える。
#
# なぜフックか：「黄色になったら保存を提案する」は AI の注意だけが頼りだった。
# 注意こそ、文脈が膨らむほど薄くなるもの。ステータスバーは人間に数字を見せ、
# このフックは AI にも見せる。帯は bin/statusline.py と同じ：黄色 18万、赤 24万。
# 標準出力は文脈になる。
set -u
input="$(cat)"
DIR="${CLAUDE_PROJECT_DIR:-$PWD}/.session"
printf '%s' "$input" | SESSION_DIR="$DIR" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
path, sid = d.get("transcript_path") or "", d.get("session_id") or ""
if not path or not sid or not os.path.isfile(path):
    sys.exit(0)
# 直近の応答の usage に、送った文脈の総量が入っている。
# 会話履歴は数十MBになるので、末尾だけ読む。
with open(path, "rb") as f:
    f.seek(0, 2)
    f.seek(max(0, f.tell() - 400_000))
    tail = f.read().decode("utf-8", "ignore").splitlines()
used = 0
for line in reversed(tail):
    if "\"usage\"" not in line:
        continue
    try:
        u = json.loads(line).get("message", {}).get("usage") or {}
    except ValueError:
        continue
    used = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    if used:
        break
band = 2 if used >= 240_000 else 1 if used >= 180_000 else 0
if not band:
    sys.exit(0)
state = os.path.join(os.environ["SESSION_DIR"], "meter-" + sid)
try:
    seen = int(open(state).read().strip() or 0)
except (OSError, ValueError):
    seen = 0
if band <= seen:
    sys.exit(0)
try:
    open(state, "w").write(str(band))
except OSError:
    pass
k = used // 1000
if band == 1:
    print(f"🟠 文脈が約{k}k（黄色）。この返答の最後に一行で提案する：「文脈が{k}kです。保存して続けますか？」はいなら `closeout` の「続ける」出口を走らせ、人間が /clear を押せば再開フックが話を戻す。")
else:
    print(f"🔴 文脈が約{k}k、作業上限の24万を超えた。本題に入る前に一行でそう伝え、`closeout`（終わっていなければ「続ける」出口）してから /clear を勧める。ここから先は品質が落ちる。")
'
exit 0
