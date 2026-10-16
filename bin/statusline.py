#!/usr/bin/env python3
"""ステータスライン。1行で「今どこにいて、何を考えていて、どれだけ文脈が膨らんでいるか」を出す。

Claude Code から標準入力で渡される JSON を読み、色つきの1行を出力する:
    <アイコン> <名前> | <ドメイン> [| <トピック>] | <モデル> | <ブランチ> [| NNNk ctx]

文脈の数字は「ウィンドウに対する割合」ではなく **絶対トークン数**。大きな
コンテキストウィンドウでも品質はずっと手前で落ちるので、割合表示は安全に
見えすぎる。18万で黄色、24万で赤（⚠wrap）。「そろそろ締めて引き継ぐ」合図を
AI自身の注意力に頼らず常時見えるようにするためのもの。

2つめの区画は **今いるドメイン**（既定はマスターモード）。セッションごとの
状態ファイル `.session/domain-<session_id>` から読む。ファイルの2行目は
**トピック**（AIが今取り組んでいることの短いラベル）で、人間が「AIが何を
考えているか」を一目で見られるようにするためのもの。セッションごとに別
ファイルなので、同じリポジトリで2つのウィンドウを開いても奪い合わない。
値が無ければその区画を省くだけなので、表示が壊れることはない。

このリポジトリの外で呼ばれたときは `cwd | モデル | ctx` の素の1行に落ちる。
ステータスラインをグローバルに設定しても、他のプロジェクトの表示が
真っ白にならないようにするため。

使い方: .claude/settings.local.json に
    "statusLine": {"type": "command", "command": "python3 <絶対パス>/bin/statusline.py"}
"""
import sys, os, json, subprocess

NAME = "{{AI_NAME}}"   # setup スキルが置き換える
ICON = "🧠"            # 好きな絵文字に変えてよい

# ドメインのスラグを、画面に出す表示名に変える表。埋めるのは任意で、
# 無ければスラグから自動で作る（job-hunt → Job Hunt）。日本語で出したい、
# 略称にしたい、というときだけ書けばよい。
# bin/menu もここを読むので、2箇所に同じ表ができてずれることはない。
DOMAIN_LABELS: dict[str, str] = {
    # "job-hunt": "求職",
}

GOLD  = "\033[38;2;230;185;70m"
DIM   = "\033[38;2;150;120;50m"
BOLD  = "\033[1m"
RESET = "\033[0m"
RED   = "\033[38;2;220;90;60m"
AMBER = "\033[38;2;220;150;50m"


def session_id_of(data):
    """このセッションのid。ハーネスごとに置き場所が違う。

    Claude Code は CLAUDE_CODE_SESSION_ID、Codex は CODEX_SESSION_ID、
    Grok Build は GROK_SESSION_ID をエージェント環境に出す。どれも無ければ
    標準入力のJSONに載っているものを使う。
    """
    return (
        os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CODEX_SESSION_ID")
        or os.environ.get("GROK_SESSION_ID")
        or data.get("session_id")
        or ""
    )


def is_os_repo(root):
    """ここがこのAI OSのリポジトリか。

    名前ではなく構造で判定する（テンプレートはAIの名前を後から変えるため）。
    憲法と知識の台帳が揃っていれば、それはこのシステムのリポジトリ。
    """
    has_rules = any(
        os.path.exists(os.path.join(root, n)) for n in ("CLAUDE.md", "AGENTS.md")
    )
    return has_rules and os.path.exists(os.path.join(root, "knowledge", "index.md"))


def context_segment(cw):
    """文脈の使用量。割合ではなく絶対トークン数で出す（理由は冒頭の説明）。"""
    if not isinstance(cw, dict):
        return None
    tin = cw.get("context_tokens")
    if not isinstance(tin, (int, float)) or tin <= 0:
        tin = cw.get("total_input_tokens")
    if not isinstance(tin, (int, float)) or tin <= 0:
        return None
    used = int(tin)
    if used >= 240_000:
        col, mark = RED, " ⚠wrap"
    elif used >= 180_000:
        col, mark = AMBER, ""
    else:
        col, mark = DIM, ""
    return f"{col}{used // 1000}k ctx{mark}{RESET}"


def generic_line(cwd, model, cw):
    """このリポジトリの外での表示。素っ気なくていいが、空にはしない。"""
    name = os.path.basename(cwd.rstrip(os.sep)) or cwd
    parts = [name, model]
    seg = context_segment(cw)
    if seg:
        parts.append(seg)
    return " | ".join(parts)


def active_domain(project_dir, session_id=None):
    """このセッションのドメインスラッグと（あれば）トピックを読む。

    1行目 = ドメインのスラッグ、2行目（任意） = 自由記述のトピック。
    ファイルが無い・空ならマスターモードとして "Master" を返す。
    """
    d = os.path.join(project_dir, ".session")
    path = os.path.join(d, "domain-" + session_id) if session_id else os.path.join(d, "domain")
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []
    slug = lines[0].strip() if lines else ""
    topic = lines[1].strip() if len(lines) > 1 else ""
    if len(topic) > 28:
        topic = topic[:27] + "…"
    if not slug:
        return "Master", topic
    label = DOMAIN_LABELS.get(slug) or slug.replace("-", " ").replace("_", " ").title()
    return label, topic


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    ws = data.get("workspace") or {}
    cwd = ws.get("current_dir") or data.get("cwd") or os.getcwd()
    project_dir = ws.get("repo_root") or ws.get("project_dir") or cwd
    session_id = session_id_of(data)
    model = (data.get("model") or {}).get("display_name") or "?"
    cw = data.get("context_window")

    if not is_os_repo(project_dir):
        sys.stdout.write(generic_line(cwd, model, cw))
        return

    domain, topic = active_domain(project_dir, session_id)

    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=1,
        ).stdout.strip()
    except Exception:
        branch = ""

    ctx_seg = context_segment(cw)

    sep = f"{DIM} | {RESET}"
    parts = [f"{GOLD}{ICON} {BOLD}{NAME}{RESET}", f"{GOLD}{domain}{RESET}"]
    if topic:
        parts.append(f"{DIM}{topic}{RESET}")
    parts.append(f"{DIM}{model}{RESET}")
    if branch:
        parts.append(f"{DIM}{branch}{RESET}")
    if ctx_seg:
        parts.append(ctx_seg)
    print(sep.join(parts))


if __name__ == "__main__":
    main()
