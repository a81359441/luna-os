#!/usr/bin/env python3
"""受信箱の整理: アーカイブ・既読・ラベル。送信・ゴミ箱・削除は一切しない。

gmail.modify 付きのトークンが要る（python3 bin/gmail/auth.py --modify）。
このファイルが行う書き込みは users.messages.batchModify（ラベルの付け外し）だけ。
grep すれば分かるとおり、送信・ゴミ箱・削除のコードパスは無い。
アーカイブ＝INBOX ラベルを外すこと。メールは「すべてのメール」に残り、
unarchive で受信トレイに戻せる。

**既定はドライラン。** どのコマンドも、何に触るかを表示して止まる。
実行するには --apply を付ける。実行した内容（メッセージIDと操作）は
.session/gmail-tidy.log に追記されるので、あとから戻せる。

使い方:
    python3 bin/gmail/tidy.py archive "in:inbox from:news@example.com"          # ドライラン
    python3 bin/gmail/tidy.py archive "in:inbox from:news@example.com" --apply
    python3 bin/gmail/tidy.py unarchive --apply                                  # 直前のアーカイブを戻す
    python3 bin/gmail/tidy.py read "in:inbox is:unread from:example.com" --apply
    python3 bin/gmail/tidy.py label "求人" "from:example.com" --apply

標準ライブラリのみ。
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gmailreader", os.path.join(HERE, "gmail.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

LOG = os.path.join(g.REPO, ".session", "gmail-tidy.log")
CAP = 1000  # batchModify の1回あたりの上限。1回の実行の安全上限も兼ねる


def post(path, payload):
    req = urllib.request.Request(
        f"{g.API}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {g.access_token()}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 403 and "insufficient" in body.lower():
            g.die("このトークンには gmail.modify がありません。実行:  python3 bin/gmail/auth.py --modify")
        g.die(f"Gmail API {e.code}: {body}")


def preview(ids, query, verb):
    print(f"{verb}: {len(ids)} 件が一致  {query}")
    tally = {}
    for mid in ids[:40]:
        h = g.headers_of(g.get(f"messages/{mid}", format="metadata", metadataHeaders=["From"]))
        sender = h.get("from", "?")
        tally[sender] = tally.get(sender, 0) + 1
    for sender, n in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {n:>3}  {sender}")
    if len(ids) > 40:
        print(f"  … 送信者は {len(ids)} 件のうち最初の40件分")


def modify(ids, add=(), remove=(), action="", query=""):
    for i in range(0, len(ids), CAP):
        post("messages/batchModify", {"ids": ids[i:i + CAP], "addLabelIds": list(add), "removeLabelIds": list(remove)})
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
                            "query": query, "ids": ids}, ensure_ascii=False) + "\n")
    print(f"✔ {action}: {len(ids)} 件。.session/gmail-tidy.log に記録しました")


def run(a, verb, add=(), remove=()):
    ids = g.search_ids(a.query, a.max)
    if not ids:
        print(f"一致するメールはありません: {a.query}")
        return
    preview(ids, a.query, verb)
    if not a.apply:
        print("\nドライランです。実行するには --apply を付けてください。")
        return
    modify(ids, add, remove, verb, a.query)


def label_id(name):
    for l in g.get("labels").get("labels", []):
        if l["name"] == name:
            return l["id"]
    return post("labels", {"name": name, "labelListVisibility": "labelShow",
                           "messageListVisibility": "show"})["id"]


def cmd_archive(a):
    run(a, "archive", remove=["INBOX"])


def cmd_read(a):
    run(a, "read", remove=["UNREAD"])


def cmd_label(a):
    if not a.apply:
        run(a, f"label {a.name}")
        return
    run(a, f"label {a.name}", add=[label_id(a.name)])


def cmd_unarchive(a):
    if not os.path.exists(LOG):
        g.die("まだ記録がありません。戻すものはありません。")
    rows = [json.loads(l) for l in open(LOG) if l.strip()]
    archives = [r for r in rows if r["action"] == "archive"]
    if not archives:
        g.die("記録にアーカイブがありません。")
    last = archives[-1]
    print(f"{last['at']} のアーカイブを戻します: {len(last['ids'])} 件  ({last['query']})")
    if not a.apply:
        print("\nドライランです。実行するには --apply を付けてください。")
        return
    modify(last["ids"], add=["INBOX"], action="unarchive", query=last["query"])


def main():
    p = argparse.ArgumentParser(description="Gmail の受信箱の整理。--apply を付けない限りドライラン。")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("archive", cmd_archive), ("read", cmd_read)):
        s = sub.add_parser(name)
        s.add_argument("query")
        s.add_argument("--max", type=int, default=500)
        s.add_argument("--apply", action="store_true")
        s.set_defaults(fn=fn)
    s = sub.add_parser("label")
    s.add_argument("name")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=500)
    s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_label)
    s = sub.add_parser("unarchive", help="直前のアーカイブを戻す")
    s.add_argument("--last", action="store_true", default=True)
    s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_unarchive)
    a = p.parse_args()
    if getattr(a, "max", 0) > CAP:
        g.die(f"--max は {CAP} まで。検索条件を絞ってください。")
    a.fn(a)


if __name__ == "__main__":
    main()
