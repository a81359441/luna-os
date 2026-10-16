#!/usr/bin/env python3
"""Gmail の読み取り専用リーダー。

**構造として読み取り専用。** このスクリプトには送信・下書き・削除・ラベル変更の
呼び出しが1つも実装されていない。投げるリクエストはすべて
users.messages / users.threads / users.labels / users.getProfile に対する GET。
grepすれば分かる。Gmail APIへのPOSTはこのファイルのどこにも無い。
auth.py が発行するトークンも既定で読み取り専用（--compose を明示しない限り）。

使い方:
    python3 bin/gmail/gmail.py whoami
    python3 bin/gmail/gmail.py labels
    python3 bin/gmail/gmail.py search "請求書" --max 50
    python3 bin/gmail/gmail.py search "newer_than:1y (お問い合わせ OR ご依頼)"
    python3 bin/gmail/gmail.py show <messageId>
    python3 bin/gmail/gmail.py dump "請求書" --max 40 --out data/gmail/corpus.md
    python3 bin/gmail/gmail.py screen "newer_than:6m ご依頼" --brands "BrandA,BrandB"

Gmailの検索構文がそのまま使える（from:, newer_than:, has:attachment, -in:chats …）。

標準ライブラリのみ。pip install は不要。
"""
import argparse
import base64
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKEN_FILE = os.environ.get("GMAIL_TOKEN", os.path.join(REPO, ".secrets", "gmail-token.json"))
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def die(msg):
    print(f"\n✖ {msg}\n", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- 認証（更新のみ）

def load_token():
    if not os.path.exists(TOKEN_FILE):
        die(f"トークンがありません: {TOKEN_FILE}\n実行してください:  python3 bin/gmail/auth.py")
    with open(TOKEN_FILE) as f:
        return json.load(f)


def access_token():
    tok = load_token()
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]

    data = urllib.parse.urlencode(
        {
            "client_id": tok["client_id"],
            "client_secret": tok["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URI, data=data)) as r:
            fresh = json.load(r)
    except urllib.error.HTTPError as e:
        die(f"トークンの更新に失敗 ({e.code}): {e.read().decode()}\nauth.py を実行し直してください。")

    tok["access_token"] = fresh["access_token"]
    tok["expires_at"] = time.time() + fresh.get("expires_in", 3600) - 60
    with open(TOKEN_FILE, "w") as f:
        json.dump(tok, f, indent=2)
    return tok["access_token"]


def get(path, **params):
    """このツールが持つ唯一のネットワーク動詞。Gmail APIへのHTTP GET。"""
    url = f"{API}/{path}"
    if params:
        pairs = []
        for k, v in params.items():
            if v is None:
                continue
            for item in v if isinstance(v, list) else [v]:
                pairs.append((k, item))
        url += "?" + urllib.parse.urlencode(pairs)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token()}"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 403 and "insufficient" in body.lower():
            die("スコープのエラー。トークンに gmail.readonly がありません。トークンファイルを消して auth.py をやり直してください。")
        die(f"Gmail API {e.code}: {body}")


# ---------------------------------------------------------------- メールの解析

def headers_of(msg):
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def b64(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s)


def body_of(msg):
    """MIMEツリーを深さ優先でたどる。text/plain を優先し、無ければHTMLを平文化。"""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain":
                plain.append(b64(data))
            elif mime == "text/html":
                rich.append(strip_html(b64(data)))
        for sub in part.get("parts", []):
            walk(sub)

    walk(msg.get("payload", {}))
    text = "\n".join(plain) or "\n".join(rich) or msg.get("snippet", "")
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def attachments_of(msg):
    names = []

    def walk(part):
        if part.get("filename"):
            names.append(part["filename"])
        for sub in part.get("parts", []):
            walk(sub)

    walk(msg.get("payload", {}))
    return names


def search_ids(query, maxn):
    ids, token = [], None
    while len(ids) < maxn:
        page = get("messages", q=query, maxResults=min(100, maxn - len(ids)), pageToken=token)
        ids += [m["id"] for m in page.get("messages", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    return ids[:maxn]


# ---------------------------------------------------------------- コマンド

# 差出人のリスクを機械的に洗い出すための材料。判断そのものは人間がする。
FREE_MAIL = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.co.jp", "yahoo.com",
    "qq.com", "163.com", "126.com", "icloud.com", "aol.com", "foxmail.com",
}
ODD_TLD = (".social", ".online", ".cc", ".cyou", ".top", ".xyz", ".icu", ".shop", ".site", ".club")
RED_FLAGS = [
    (r"銀行|口座|振込先|bank account|SWIFT", "銀行口座を聞いてくる"),
    (r"前払い金|保証金|登録料|手数料をお支払|registration fee", "こちらに金銭を要求している"),
    (r"マイナンバー|パスポート|身分証|運転免許", "身分証を要求している"),
    (r"アプリをダウンロード|\.apk|インストールしてください", "アプリのインストールを要求している"),
    (r"whatsapp|telegram|WeChat|微信|LINEでご連絡", "外部の連絡手段に誘導している"),
    (r"新しく作成|新規アカウントを|専用のアカウントを", "新規アカウントの作成を求めている"),
    (r"パスワード|ログイン情報|認証コード", "認証情報を要求している"),
]


def cmd_screen(a):
    """差出人のリスクを洗い出す。順位づけをするだけで、判断は人間がする。"""
    brands = [b.strip() for b in (a.brands or "").split(",") if b.strip()]
    me = get("profile").get("emailAddress", "").lower()
    ids = search_ids(a.query, a.max)
    out = []
    for i, mid in enumerate(ids, 1):
        m = get(f"messages/{mid}", format="full")
        h = headers_of(m)
        frm = h.get("from", "")
        if me and me in frm.lower():
            continue  # 自分が送ったメールは飛ばす
        em = re.search(r"[\w\.\-\+]+@([\w\.\-]+)", frm)
        if not em:
            continue
        dom = em.group(1).lower()
        disp = frm.split("<")[0].strip().strip('"')
        text = (h.get("subject", "") + "\n" + body_of(m))[:6000]
        claimed = [b for b in brands if b.lower() in text.lower()]

        risk, score = [], 0
        if dom in FREE_MAIL:
            if claimed:
                risk.append(f"フリーメールなのに {'/'.join(sorted(set(claimed))[:3])} を名乗っている")
                score += 5
            else:
                risk.append("フリーメール")
                score += 2
        if dom.endswith(ODD_TLD):
            risk.append("使い捨てに使われがちなTLD")
            score += 1
        rt = h.get("reply-to", "")
        if rt and dom.split(".")[-2] not in rt:
            risk.append("返信先が別のドメインを向いている")
            score += 2
        if "于" in text and "写道" in text:
            risk.append("中国語ロケールのメールクライアント")
            score += 2
        for pat, label in RED_FLAGS:
            if re.search(pat, text, re.I):
                risk.append(label)
                score += 3
        if risk:
            out.append((score, disp, dom, h.get("subject", "")[:52], risk, mid))
        print(f"  確認中 {i}/{len(ids)}", end="\r", file=sys.stderr)
    print(" " * 30, end="\r", file=sys.stderr)

    out.sort(reverse=True)
    if not out:
        print("リスクの兆候は見つかりませんでした。")
        return
    for score, disp, dom, subj, risk, mid in out:
        band = "拒否" if score >= 5 else ("要確認" if score >= 3 else "参考")
        print(f"\n[{band}  スコア {score}]  {disp} <{dom}>   id {mid}")
        print(f"  件名: {subj}")
        for r in risk:
            print(f"  - {r}")
    print(f"\n兆候のあった差出人 {len(out)} 件。拒否＝関わらない。"
          "要確認＝会社ドメインからの返信を求めてから話を進める。")


def cmd_whoami(a):
    p = get("profile")
    print(f"{p['emailAddress']}   メール数={p['messagesTotal']}  スレッド数={p['threadsTotal']}")


def cmd_labels(a):
    for l in sorted(get("labels").get("labels", []), key=lambda x: x["name"]):
        print(f"{l['id']:<28} {l['name']}")


def cmd_search(a):
    ids = search_ids(a.query, a.max)
    rows = []
    for i, mid in enumerate(ids, 1):
        m = get(
            f"messages/{mid}",
            format="metadata",
            metadataHeaders=["From", "Subject", "Date", "To"],
        )
        h = headers_of(m)
        rows.append(
            {
                "id": mid,
                "date": h.get("date", ""),
                "from": h.get("from", ""),
                "subject": h.get("subject", "(件名なし)"),
                "snippet": html.unescape(m.get("snippet", "")),
                "labels": m.get("labelIds", []),
            }
        )
        print(f"  取得中 {i}/{len(ids)}", end="\r", file=sys.stderr)
    print(" " * 30, end="\r", file=sys.stderr)

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print(f"該当なし: {a.query}")
        return
    for r in rows:
        print(f"\n[{r['id']}]  {r['date']}")
        print(f"  差出人: {r['from']}")
        print(f"  件名:   {r['subject']}")
        print(f"  {r['snippet'][:200]}")
    print(f"\n{len(rows)} 件。")


def render(m):
    h = headers_of(m)
    att = attachments_of(m)
    out = [
        f"### {h.get('subject', '(件名なし)')}",
        "",
        f"- **id**: `{m['id']}`",
        f"- **from**: {h.get('from', '')}",
        f"- **to**: {h.get('to', '')}",
        f"- **date**: {h.get('date', '')}",
    ]
    if h.get("reply-to"):
        out.append(f"- **reply-to**: {h['reply-to']}")
    if att:
        out.append(f"- **添付**: {', '.join(att)}")
    out += ["", "```", body_of(m), "```", ""]
    return "\n".join(out)


def cmd_show(a):
    print(render(get(f"messages/{a.id}", format="full")))


def cmd_dump(a):
    ids = search_ids(a.query, a.max)
    chunks = []
    for i, mid in enumerate(ids, 1):
        chunks.append(render(get(f"messages/{mid}", format="full")))
        print(f"  取得中 {i}/{len(ids)}", end="\r", file=sys.stderr)
    print(" " * 30, end="\r", file=sys.stderr)

    doc = (
        f"# Gmail ダンプ  検索: `{a.query}`\n\n"
        f"{len(chunks)} 件。生の一次情報。編集しないこと。\n\n---\n\n"
        + "\n---\n\n".join(chunks)
    )
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            f.write(doc)
        print(f"✔ {len(chunks)} 件 → {a.out}")
    else:
        print(doc)


def main():
    p = argparse.ArgumentParser(description="読み取り専用のGmailリーダー。")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="どのアカウントのトークンか確認する").set_defaults(fn=cmd_whoami)
    sub.add_parser("labels", help="ラベル一覧").set_defaults(fn=cmd_labels)

    s = sub.add_parser("search", help="Gmailの検索構文で一覧を出す")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=25)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("screen", help="差出人のなりすまし・詐欺の兆候を洗い出す")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=40)
    s.add_argument("--brands", help="なりすましを警戒するブランド名をカンマ区切りで")
    s.set_defaults(fn=cmd_screen)

    s = sub.add_parser("show", help="1通の全文を表示")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("dump", help="検索結果の全文をmarkdownで書き出す")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=30)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_dump)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
