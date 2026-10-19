#!/usr/bin/env python3
"""カレンダー層。自分の Google カレンダーの中に、AI専用のレイヤを1枚作る。

**書き込むのは専用カレンダーだけ。** このファイルの書き込み経路はすべて
`.session/gcal.json` に記録されたアプリ作成カレンダーを指す。"primary" にも
他のカレンダーidにも絶対に書かない。OAuth トークンの側でも独立に同じことを
強制している: calendar.app.created（アプリが作ったカレンダーにのみ書ける）と
calendar.readonly（読みはどこでも）の組み合わせなので、**ここにバグがあっても
自分で作った予定には手が届かない**。

**絶対日付のみ。** このツールは「来週の木曜」を受け付けない。呼ぶ側が先に
日付を解決すること。そして書き込みのたびに解決後の日付と曜日を読み上げる。
**黙って間違っている日付は、カレンダーが無いことより悪い**から。

使い方:
    python3 bin/gcal/gcal.py whoami
    python3 bin/gcal/gcal.py init
    python3 bin/gcal/gcal.py agenda --days 14 [--all]
    python3 bin/gcal/gcal.py add "面談" --date 2026-10-02 --start 10:00 --dur 60 \
        --where "https://meet.google.com/xxx" --tag COMMERCIAL \
        --source job-hunt/interview-pack.md
    python3 bin/gcal/gcal.py deadline "ハンドブック" --date 2026-10-01 --lead 7,1
    python3 bin/gcal/gcal.py rm <キー>
    python3 bin/gcal/gcal.py move <キー> --date 2026-10-03 [--start 11:00]

書き込み系はどれも --dry-run を足すと、送る中身だけを表示して何も送らない。

標準ライブラリだけで動く。pip install は不要。
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    print("✖ zoneinfo の使える Python 3.9 以上が必要です。", file=sys.stderr)
    sys.exit(1)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main_checkout(repo):
    """worktree の中でも、鍵と状態は本体のチェックアウトにある。

    `.secrets/` と `.session/` は gitignore 済みなので、新しい worktree には来ない。
    worktree の `.git` はファイルで、`gitdir: <本体>/.git/worktrees/<名前>` を指す。
    そこから本体を割り出す（2026-10-19、worktree でブリーフがカレンダーに届かなかった）。
    """
    g = os.path.join(repo, ".git")
    if os.path.isfile(g):
        with open(g, encoding="utf-8") as f:
            line = f.read().strip()
        if line.startswith("gitdir:"):
            gd = os.path.normpath(os.path.join(repo, line[len("gitdir:"):].strip()))
            if os.path.basename(os.path.dirname(gd)) == "worktrees":
                return os.path.dirname(os.path.dirname(os.path.dirname(gd)))
    return repo


HOME = main_checkout(REPO)
TOKEN_FILE = os.environ.get("GCAL_TOKEN", os.path.join(HOME, ".secrets", "gcal-token.json"))
STATE_FILE = os.environ.get("GCAL_STATE", os.path.join(HOME, ".session", "gcal.json"))
API = "https://www.googleapis.com/calendar/v3"
TOKEN_URI = "https://oauth2.googleapis.com/token"

NAME = "{{AI_NAME}}"   # setup スキルが置き換える
CAL_NAME = NAME        # カレンダー一覧に出る名前。AIと同じ名前にしておく
DEFAULT_TZ = os.environ.get("AI_TZ", "Asia/Tokyo")
WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]

# 境界タグ。憲法の Declare → Tag → Confirm と同じ語彙を使うこと。
# 自分の事業や案件を足すならここに足す（例: 屋号）。
TAGS = ["PERSONAL", "CONTENT", "COMMERCIAL", "HYBRID"]

# Google の11色。色は **--purpose**（これは何の種類の予定か）で決まる。
# **--tag**（誰の境界に属するか）は別の軸で、色には影響しない。
PURPOSE = {
    "work":     ("9",  "blueberry"),   # 商談, 面談, 顧客の仕事, 定例
    "deadline": ("11", "tomato"),      # 締切
    "event":    ("6",  "tangerine"),   # 本番, フェス, 集まり
    "travel":   ("8",  "graphite"),    # 移動, 運転, フライト, 帰り
    "prep":     ("5",  "banana"),      # 言われたのではなくAIが導いた準備ブロック
    "life":     ("10", "basil"),       # 生活, 用事, 手続き
    "content":  ("3",  "grape"),       # 撮影, 編集, 投稿
    "health":   ("4",  "flamingo"),    # ジム, 通院
    "focus":    ("7",  "peacock"),     # その日の評価が決まる「今日の一点」
    "daily":    ("1",  "lavender"),    # 繰り返しの土台: ジム, 連絡, 終い
}


def die(msg):
    print(f"\n✖ {msg}\n", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- auth (refresh only)

def seed_token():
    """クラウドのコンテナにはトークンのファイルが無い。あるのは環境が渡すものだけ。

    GCAL_TOKEN_JSON には、手元で auth.py が書くのと同じ JSON を入れる（環境の
    シークレットとして。リポジトリには絶対に入れない）。一度だけファイルに書き出し、
    以降は手元と同じように、下の更新処理がアクセストークンを保つ。
    """
    raw = os.environ.get("GCAL_TOKEN_JSON", "").strip()
    if os.path.exists(TOKEN_FILE) or not raw:
        return
    try:
        json.loads(raw)  # 壊れたシークレットは、ゴミを書く前に断る
    except ValueError:
        die("GCAL_TOKEN_JSON が正しい JSON ではありません。トークンのファイルを丸ごと貼ってください。")
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(raw)


def have_token():
    seed_token()
    return os.path.exists(TOKEN_FILE)


def load_token():
    if not have_token():
        die(f"トークンがありません: {TOKEN_FILE}\n実行してください:  python3 bin/gcal/auth.py  （または GCAL_TOKEN_JSON を設定）")
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
        body = e.read().decode()
        hint = ""
        if "invalid_grant" in body:
            hint = (
                "\n\nGoogle 側のアプリが「テスト」モードのままだと、トークンは7日で切れます。\n"
                "もう一度:  python3 bin/gcal/auth.py"
            )
        die(f"トークンの更新に失敗しました ({e.code}): {body}{hint}")

    tok["access_token"] = fresh["access_token"]
    tok["expires_at"] = time.time() + fresh.get("expires_in", 3600) - 60
    with open(TOKEN_FILE, "w") as f:
        json.dump(tok, f, indent=2)
    return tok["access_token"]


def api(method, path, body=None, **params):
    url = f"{API}/{path}"
    pairs = []
    for k, v in params.items():
        if v is None:
            continue
        for item in v if isinstance(v, list) else [v]:
            pairs.append((k, item))
    if pairs:
        url += "?" + urllib.parse.urlencode(pairs)

    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {access_token()}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
        # 書き込みのたびに、起動時ブリーフの3時間キャッシュを捨てる。
        # 捨てないと、締めで予定を消して /clear した直後のブリーフが、
        # 消したはずの予定を出してしまう。次のブリーフで取り直させる。
        if method != "GET":
            for root in {REPO, HOME}:
                try:
                    os.remove(os.path.join(root, ".session", "cache", "agenda.json"))
                except OSError:
                    pass
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()
        if e.code == 403 and "has not been used in project" in body_txt:
            m = re.search(r"https://console\.developers\.google\.com\S+?(?=[\"\s\\])", body_txt)
            die(
                "この Cloud プロジェクトで Google Calendar API がまだ有効になっていません。\n"
                "ブラウザで一度これを開き、Enable を押して1分待ってから、もう一度:\n\n"
                f"    {m.group(0) if m else 'https://console.cloud.google.com/apis/library/calendar-json.googleapis.com'}"
            )
        if e.code in (401, 403) and ("insufficient" in body_txt.lower() or "ACCESS_TOKEN_SCOPE" in body_txt):
            die(
                "スコープ不足: この呼び出しに必要な権限をトークンが持っていません。\n"
                f"{TOKEN_FILE} を消して、もう一度:  python3 bin/gcal/auth.py\n"
                "（スコープは認可した瞬間に焼き付きます。auth.py を編集しただけでは何も変わりません）"
            )
        die(f"Calendar API エラー {e.code} / {method} {path}:\n{body_txt}")


# ---------------------------------------------------------------- state

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def own_cal_id(required=True):
    state = load_state()
    cal = state.get("calendar_id")
    if not cal and have_token():
        # まっさらなチェックアウト（クラウドのセッション）には、トークンはあっても
        # 状態ファイルが無い。2つ目を作らず、既にあるカレンダーを名前で探す。
        found = [c for c in calendars() if c.get("summary") == CAL_NAME]
        if found:
            cal = found[0]["id"]
            state.update(calendar_id=cal, calendar_name=CAL_NAME)
            save_state(state)
    if not cal and required:
        die(f"{NAME} のカレンダーがまだありません。\n実行してください:  python3 bin/gcal/gcal.py init")
    return cal


# ---------------------------------------------------------------- date helpers

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def want_date(s, field="--date"):
    """絶対日付 YYYY-MM-DD のみ。相対的な言い方は**意図的に**弾く。"""
    if not s or not DATE_RE.match(s):
        die(
            f"{field} は YYYY-MM-DD の絶対日付でなければなりません。受け取った値: {s!r}\n"
            f"呼ぶ前に日付を解決してください。今日は {date.today().isoformat()} "
            f"({stamp(date.today())}) です。"
        )
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        die(f"{field} は実在しない日付です: {s!r}")


def want_time(s, field="--start"):
    if not s or not TIME_RE.match(s):
        die(f"{field} は24時間表記の HH:MM です。受け取った値: {s!r}")
    h, m = s.split(":")
    return int(h), int(m)


def stamp(d):
    """「Thu 木」形式の曜日。間違った日付が一目で分かるようにするため。"""
    return f"{d.strftime('%a')} {WEEKDAY_JP[d.weekday()]}"


def slug(s, limit=40):
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w぀-ヿ一-鿿]+", "-", s, flags=re.UNICODE).strip("-")
    return s[:limit] or "event"


def tz_of(name):
    try:
        return ZoneInfo(name)
    except Exception:
        die(f"知らないタイムゾーンです: {name!r}。Asia/Tokyo のような IANA 名を使ってください。")


# ---------------------------------------------------------------- reads

def calendars():
    items = api("GET", "users/me/calendarList", maxResults=250).get("items", [])
    return items


def events_between(cal_id, start_dt, end_dt, **extra):
    out, page = [], None
    while True:
        res = api(
            "GET",
            f"calendars/{urllib.parse.quote(cal_id)}/events",
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents="true",
            orderBy="startTime",
            maxResults=250,
            pageToken=page,
            **extra,
        )
        out.extend(res.get("items", []))
        page = res.get("nextPageToken")
        if not page:
            break
    return out


def find_by_key(cal_id, key):
    """upsert のための検索。1予定につき安定キー1つ。全期間から探す。"""
    res = api(
        "GET",
        f"calendars/{urllib.parse.quote(cal_id)}/events",
        privateExtendedProperty=f"aiKey={key}",
        showDeleted="false",
        maxResults=5,
    )
    items = res.get("items", [])
    return items[0] if items else None


def when_of(ev):
    s = ev.get("start", {})
    if "date" in s:
        d = datetime.strptime(s["date"], "%Y-%m-%d").date()
        return d, None
    dt = datetime.fromisoformat(s["dateTime"])
    return dt.date(), dt.strftime("%H:%M")


def span_of(ev):
    """終日予定の最終日（その日を含む）。Google は end を「含まない」で保存する。"""
    s, e = ev.get("start", {}), ev.get("end", {})
    if "date" not in s or "date" not in e:
        return None
    last = datetime.strptime(e["date"], "%Y-%m-%d").date() - timedelta(days=1)
    first = datetime.strptime(s["date"], "%Y-%m-%d").date()
    return last if last > first else None


def show_events(evs, label=None):
    for ev in evs:
        d, t = when_of(ev)
        last = span_of(ev)
        when = f"{d.isoformat()} ({stamp(d)})"
        if last:
            when += f" .. {last:%m-%d} ({stamp(last)})"
        slot = f"{t:>5}" if t else "全日 "
        priv = ev.get("extendedProperties", {}).get("private", {})
        key, tag, pur = priv.get("aiKey", ""), priv.get("aiTag", ""), priv.get("aiPurpose", "")
        bits = [when, slot, ev.get("summary", "(no title)")]
        if pur:
            bits.append(f"[{pur}]")
        if tag:
            bits.append(f"#{tag}")
        if label:
            bits.append(f"[{label}]")
        if key:
            bits.append(f"· {key}")
        print("  " + "  ".join(bits))


# ---------------------------------------------------------------- commands

def cmd_whoami(a):
    tok = load_token()
    cals = calendars()
    primary = next((c for c in cals if c.get("primary")), None)
    print(f"\nアカウント : {primary['id'] if primary else '(primary カレンダーが見えません)'}")
    state = load_state()
    cal_id = state.get("calendar_id")
    if cal_id:
        mine = next((c for c in cals if c["id"] == cal_id), None)
        print(f"専用カレンダー : {state.get('calendar_name', CAL_NAME)}  ({cal_id})")
        if not mine:
            print("          ⚠ カレンダー一覧にありません。UIで消しましたか？ init をやり直してください。")
    else:
        print("専用カレンダー : (まだありません。gcal.py init を実行してください)")
    print(f"タイムゾーン : {DEFAULT_TZ}")
    print(f"見えている数 : {len(cals)} 個")
    print("\n許可されているスコープ:")
    for s in tok.get("scope", "").split():
        note = ""
        if s.endswith("calendar.app.created"):
            note = "   (書き込み。専用カレンダーのみ)"
        elif s.endswith("calendar.readonly"):
            note = "   (read, every calendar)"
        print(f"  · {s}{note}")
    if "calendar.app.created" not in tok.get("scope", ""):
        print("\n⚠ 読み取り専用のトークンです。書き込みは失敗します。--readonly を外して auth.py をやり直してください。")
    print()


def cmd_init(a):
    state = load_state()
    if state.get("calendar_id") and not a.force:
        cals = {c["id"] for c in calendars()}
        if state["calendar_id"] in cals:
            print(f"✔ 専用カレンダーは既にあります: {state['calendar_id']}")
            return
        print("⚠ 記録されていたカレンダーがアカウントから消えています。新しく作り直します。")

    if a.dry_run:
        print(f"[dry-run] カレンダー {CAL_NAME!r} を作ります (tz {a.tz})")
        return

    cal = api("POST", "calendars", {"summary": CAL_NAME, "timeZone": a.tz,
                                    "description": f"{NAME} が知ったことをここに入れます。非表示にしても削除しても構いません。"})
    state["calendar_id"] = cal["id"]
    state["calendar_name"] = cal.get("summary", CAL_NAME)
    state["timezone"] = a.tz
    save_state(state)
    print(f"✔ カレンダー {cal.get('summary')!r} を作りました")
    print(f"  id: {cal['id']}")
    print(f"  保存先: {STATE_FILE}")
    print("\nスマホでは「他のカレンダー」に出ます。反映されないときは画面を引っ張って")
    print("更新するか、Google カレンダーアプリの設定で一度オンにしてください。")


def ignored():
    """他人のカレンダー。読めはするが、表示も推論もしない。"""
    return set(load_state().get("ignore_calendars", []))


def as_rows(evs):
    """素の dict にした予定。他のツールが読むため（bin/menu のブリーフ）。"""
    out = []
    for ev in evs:
        d, t = when_of(ev)
        last = span_of(ev)
        priv = ev.get("extendedProperties", {}).get("private", {})
        out.append({
            "date": d.isoformat(),
            "until": last.isoformat() if last else None,
            "time": t or None,
            "title": ev.get("summary", "(no title)"),
            "purpose": priv.get("aiPurpose", ""),
            "tag": priv.get("aiTag", ""),
            "source": priv.get("aiSource", ""),
            "key": priv.get("aiKey", ""),
        })
    return out


def other_calendars(own_id):
    """自分のレイヤ以外で読んでいいカレンダー。無視リストと祝日カレンダーは除く。"""
    skip = ignored()
    return [c for c in calendars()
            if c["id"] != own_id and c.get("summary") not in skip and c["id"] not in skip
            and "#holiday@" not in c["id"]]


def declined(ev):
    return any(p.get("self") and p.get("responseStatus") == "declined" for p in ev.get("attendees", []))


def cmd_agenda(a):
    tz = tz_of(a.tz)
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=a.days)
    cal_id = own_cal_id()

    if getattr(a, "json", False):
        rows = as_rows(events_between(cal_id, start, end))
        if a.all:
            # ブリーフは本人が手で入れた予定も読む。AI のカレンダーだけ見て「予定なし」と
            # 出すのは、読めない日より悪い（安心させてしまう。2026-10-19 に実際に起きた）。
            for c in other_calendars(cal_id):
                rows += as_rows([ev for ev in events_between(c["id"], start, end) if not declined(ev)])
            rows.sort(key=lambda r: (r["date"], r["time"] or ""))
        print(json.dumps(rows, ensure_ascii=False))
        return

    print(f"\n=== {NAME} レイヤ · {start.date()} 〜 {end.date()} ({a.days}日) ===")
    evs = events_between(cal_id, start, end)
    if evs:
        show_events(evs)
    else:
        print("  (予定なし)")

    if a.all:
        for c in other_calendars(cal_id):
            evs = [ev for ev in events_between(c["id"], start, end) if not declined(ev)]
            if not evs:
                continue
            print(f"\n=== {c.get('summary', c['id'])} ===")
            show_events(evs)
    print()


MAX_REMIND = 40320  # Google の上限。予定の28日前まで


def _reminders(minutes_list):
    out = []
    for m in minutes_list:
        if m > MAX_REMIND:
            print(f"⚠ {m//1440}日前の通知は Google の上限28日を超えるので、28日に丸めました。")
            m = MAX_REMIND
        out.append({"method": "popup", "minutes": max(0, m)})
    return {"useDefault": False, "overrides": out[:5]}  # Google allows 5 per event


def _purpose(name, default):
    name = (name or default).lower()
    if name not in PURPOSE:
        die(f"--purpose は次のどれかです: {', '.join(PURPOSE)}")
    return name, PURPOSE[name][0]


def _extended(key, tag, source, purpose=None):
    priv = {"aiKey": key}
    if purpose:
        priv["aiPurpose"] = purpose
    if tag:
        priv["aiTag"] = tag
    if source:
        priv["aiSource"] = source
    return {"private": priv}


def _describe(desc, source):
    parts = [p for p in [desc] if p]
    if source:
        parts.append(f"出典: {source}")
    parts.append(f"{NAME} が入れた予定です。自由に編集・削除してください。次回以降、辻褄は合わせます。")
    return "\n\n".join(parts)


def _upsert(cal_id, key, body, dry_run, headline):
    if dry_run:
        print(f"[dry-run] {headline}")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    existing = find_by_key(cal_id, key)
    if existing:
        # PATCH replaces extendedProperties wholesale, so an update that omits
        # --tag/--source would silently drop them. Carry the old values forward.
        was = existing.get("extendedProperties", {}).get("private", {})
        merged = {**was, **body.get("extendedProperties", {}).get("private", {})}
        body["extendedProperties"] = {"private": merged}
        ev = api("PATCH", f"calendars/{urllib.parse.quote(cal_id)}/events/{existing['id']}", body)
        print(f"↻ 更新     {headline}")
    else:
        ev = api("POST", f"calendars/{urllib.parse.quote(cal_id)}/events", body)
        print(f"✔ 登録     {headline}")
    print(f"           キー: {key}")
    return ev


def cmd_add(a):
    d = want_date(a.date)
    hh, mm = want_time(a.start)
    tz = a.tz
    start_dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz_of(tz))
    if a.end:
        eh, em = want_time(a.end, "--end")
        end_dt = datetime(d.year, d.month, d.day, eh, em, tzinfo=tz_of(tz))
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
    else:
        end_dt = start_dt + timedelta(minutes=a.dur)

    key = a.key or f"{slug(a.title)}-{d.isoformat()}"
    if a.tag and a.tag not in TAGS:
        die(f"--tag は次のどれかです: {', '.join(TAGS)}")
    pur, color = _purpose(a.purpose, "life")

    body = {
        "summary": a.title,
        "description": _describe(a.desc, a.source),
        "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
        "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
        "colorId": color,
        "reminders": _reminders([int(x) for x in a.remind.split(",") if x.strip()]),
        "extendedProperties": _extended(key, a.tag, a.source, pur),
    }
    if a.where:
        body["location"] = a.where

    head = f"{d.isoformat()} ({stamp(d)}) {start_dt:%H:%M}-{end_dt:%H:%M} {tz}  {a.title}  [{pur}]"
    _upsert(own_cal_id(required=not a.dry_run), key, body, a.dry_run, head)


def _allday(a, is_event):
    """2つの動詞の共通実装。`deadline`（期限の印）と
    `allday`（実際に起きること。複数日にまたがることもある）。"""
    d = want_date(a.date)
    until = want_date(a.until, "--until") if getattr(a, "until", None) else d
    if until < d:
        die(f"--until ({until}) が --date ({d}) より前です。")

    prefix = "" if is_event else "due-"
    key = a.key or f"{prefix}{slug(a.title)}-{d.isoformat()}"
    if a.tag and a.tag not in TAGS:
        die(f"--tag must be one of: {', '.join(TAGS)}")
    pur, color = _purpose(a.purpose, "event" if is_event else "deadline")

    lead_days = [int(x) for x in a.lead.split(",") if x.strip()]
    # For an all-day event Google counts reminder minutes back from midnight of
    # the start day, so N days of lead lands at 08:00 on the day N-1 days before.
    mins = sorted({max(0, n * 1440 - 480) for n in lead_days}, reverse=True)

    body = {
        "summary": a.title if is_event else f"⏰ {a.title}",
        "description": _describe(a.desc, a.source),
        # All-day events use an exclusive end date, hence the +1.
        "start": {"date": d.isoformat()},
        "end": {"date": (until + timedelta(days=1)).isoformat()},
        "colorId": color,
        "reminders": _reminders(mins),
        "extendedProperties": _extended(key, a.tag, a.source, pur),
        "transparency": "opaque" if is_event else "transparent",
    }
    span = d.isoformat() if until == d else f"{d.isoformat()} .. {until.isoformat()} ({(until - d).days + 1}d)"
    head = (f"{span} ({stamp(d)}) 全日  {'' if is_event else '⏰ '}{a.title}  [{pur}]"
            f"   lead: {', '.join(str(n) + 'd' for n in lead_days)}")
    _upsert(own_cal_id(required=not a.dry_run), key, body, a.dry_run, head)


def cmd_deadline(a):
    _allday(a, is_event=False)


def cmd_allday(a):
    _allday(a, is_event=True)


# ---------------------------------------------------------------- cross-calendar

def _resolve(a):
    """コマンドが指している予定を、どのカレンダーからでも見つける。

    キー指定  → 専用レイヤ（通常の経路）。
    --id 指定 → どのカレンダーでも。自分で手入れしているものも含む。
                その場合は**手を触れる前に声に出して名前を言う**。
                本物の予定を消せるトークンが、それを黙ってやってはいけない。
    """
    if a.id:
        cal_id = a.cal or "primary"
        ev = api("GET", f"calendars/{urllib.parse.quote(cal_id)}/events/{a.id}")
        return cal_id, ev, cal_id != own_cal_id(required=False)
    if not a.key:
        die("キーを渡すか、自分で作った予定なら --id <eventId> [--cal <calendarId>] を渡してください。")
    cal_id = own_cal_id()
    ev = find_by_key(cal_id, a.key)
    if not ev:
        die(f"キー {a.key!r} の予定がありません。一覧:  gcal.py agenda --days 365")
    return cal_id, ev, False


def cmd_find(a):
    """全カレンダーを文字列で検索する。rm / move 用の eventId を得る手段。"""
    tz = tz_of(a.tz)
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=a.back)
    end = start + timedelta(days=a.days + a.back)
    skip = ignored()
    hits = 0
    for c in calendars():
        if c.get("summary") in skip or c["id"] in skip:
            continue
        evs = events_between(c["id"], start, end, q=a.text)
        if not evs:
            continue
        print(f"\n=== {c.get('summary', c['id'])} ===")
        for ev in evs:
            d, t = when_of(ev)
            print(f"  {d.isoformat()} ({stamp(d)})  {t or '全日 ':>5}  {ev.get('summary','(無題)')}")
            print(f"      --id {ev['id']} --cal {c['id']}")
            hits += 1
    print(f"\n{a.text!r} に {hits} 件ヒット\n" if hits else f"\n{a.text!r} は見つかりませんでした。\n")


def cmd_rm(a):
    cal_id, ev, foreign = _resolve(a)
    d, t = when_of(ev)
    head = f"{d.isoformat()} ({stamp(d)}) {t or '全日'}  {ev.get('summary')}"
    if foreign:
        print(f"⚠ これは {NAME} が作った予定では**ありません**。置き場所: {cal_id}")
    if a.dry_run:
        print(f"[dry-run] 削除します  {head}")
        return
    api("DELETE", f"calendars/{urllib.parse.quote(cal_id)}/events/{ev['id']}")
    print(f"✖ 削除     {head}")
    if foreign:
        print("           Google カレンダーのゴミ箱から30日間は戻せます。")


def cmd_move(a):
    cal_id, ev, foreign = _resolve(a)
    d = want_date(a.date)
    old_d, old_t = when_of(ev)

    if "date" in ev.get("start", {}) and not a.start:
        span = (datetime.strptime(ev["end"]["date"], "%Y-%m-%d").date()
                - datetime.strptime(ev["start"]["date"], "%Y-%m-%d").date())
        body = {"start": {"date": d.isoformat()},
                "end": {"date": (d + span).isoformat()}}
        new_t = None
    elif "date" in ev.get("start", {}):
        # all-day -> timed, once a real time is known for something vague
        hh, mm = want_time(a.start)
        tz = a.tz
        start_dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz_of(tz))
        end_dt = start_dt + timedelta(minutes=a.dur or 60)
        if a.end:
            eh, em = want_time(a.end, "--end")
            end_dt = datetime(d.year, d.month, d.day, eh, em, tzinfo=tz_of(tz))
        # A PATCH that only adds dateTime leaves the old all-day `date` in place and
        # Google rejects it as "Invalid start time". The null clears it.
        body = {"start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz, "date": None},
                "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz, "date": None}}
        new_t = f"{start_dt:%H:%M}-{end_dt:%H:%M}"
    else:
        start_src = ev["start"]["dateTime"]
        hh, mm = want_time(a.start) if a.start else (int(start_src[11:13]), int(start_src[14:16]))
        tz = ev["start"].get("timeZone", a.tz)
        dur = datetime.fromisoformat(ev["end"]["dateTime"]) - datetime.fromisoformat(start_src)
        start_dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz_of(tz))
        end_dt = start_dt + dur
        body = {"start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
                "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz}}
        new_t = start_dt.strftime("%H:%M")

    head = (f"{ev.get('summary')}: {old_d.isoformat()} ({stamp(old_d)}) {old_t or '全日'}"
            f"  ->  {d.isoformat()} ({stamp(d)}) {new_t or '全日'}")
    if foreign:
        print(f"⚠ これは {NAME} が作った予定では**ありません**。置き場所: {cal_id}")
    if a.dry_run:
        print(f"[dry-run] 移動します  {head}")
        return
    api("PATCH", f"calendars/{urllib.parse.quote(cal_id)}/events/{ev['id']}", body)
    print(f"↻ 移動     {head}")


def cmd_colors(a):
    print("\n色は --purpose で決まります。--tag は別の軸（誰の境界か）です。\n")
    for name, (cid, colour) in PURPOSE.items():
        print(f"  {name:<9} {colour:<10} (colorId {cid})")
    print()


# ---------------------------------------------------------------- cli

def main():
    p = argparse.ArgumentParser(prog="gcal.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, write=False):
        sp.add_argument("--tz", default=DEFAULT_TZ, help=f"IANA タイムゾーン (既定 {DEFAULT_TZ})")
        if write:
            sp.add_argument("--dry-run", action="store_true", help="送る中身だけ表示して、何も送らない")

    def meta(sp, default_purpose):
        sp.add_argument("--purpose", help=f"種類で色分け: {', '.join(PURPOSE)} (既定 {default_purpose})")
        sp.add_argument("--desc", help="自由記述")
        sp.add_argument("--tag", help=f"境界タグ: {', '.join(TAGS)}")
        sp.add_argument("--source", help="これの出所になったリポジトリ内のパス")
        sp.add_argument("--key", help="upsert 用の安定キー")

    s = sub.add_parser("whoami", help="アカウント・専用カレンダー・許可スコープ")
    s.set_defaults(fn=cmd_whoami)

    s = sub.add_parser("colors", help="purpose と色の対応表")
    s.set_defaults(fn=cmd_colors)

    s = sub.add_parser("init", help="専用カレンダーを作る（何度実行しても安全）")
    common(s, write=True)
    s.add_argument("--force", action="store_true", help="既にあっても新しく作る")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("agenda", help="これから何があるか")
    common(s)
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--all", action="store_true", help="他のカレンダーも含める")
    s.add_argument("--json", action="store_true",
                   help="機械可読の行。--all と併せて全カレンダー（bin/menu が使う）")
    s.set_defaults(fn=cmd_agenda)

    s = sub.add_parser("find", help="全カレンダーを文字列検索して --id を得る")
    common(s)
    s.add_argument("text")
    s.add_argument("--days", type=int, default=120, help="何日先まで見るか (既定 120)")
    s.add_argument("--back", type=int, default=7, help="何日前まで遡るか (既定 7)")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("add", help="時刻のある予定を入れる")
    common(s, write=True)
    meta(s, "life")
    s.add_argument("title")
    s.add_argument("--date", required=True, help="YYYY-MM-DD。絶対日付のみ")
    s.add_argument("--start", required=True, help="HH:MM")
    s.add_argument("--end", help="HH:MM（既定: 開始から --dur 分後）")
    s.add_argument("--dur", type=int, default=60, help="分 (既定 60)")
    s.add_argument("--where", help="場所、または会議のURL")
    s.add_argument("--remind", default="180", help="何分前に通知するか (既定 180＝3時間前)")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("deadline", help="終日の「締切」マーカーを置く")
    common(s, write=True)
    meta(s, "deadline")
    s.add_argument("title")
    s.add_argument("--date", required=True, help="YYYY-MM-DD。絶対日付のみ")
    s.add_argument("--lead", default="7,1", help="何日前に警告するか。カンマ区切り (既定 7,1)")
    s.set_defaults(fn=cmd_deadline)

    s = sub.add_parser("allday", help="終日の予定を入れる。複数日にまたがってもよい")
    common(s, write=True)
    meta(s, "event")
    s.add_argument("title")
    s.add_argument("--date", required=True, help="初日。YYYY-MM-DD")
    s.add_argument("--until", help="最終日。YYYY-MM-DD（その日を含む。既定は同日）")
    s.add_argument("--lead", default="7,1", help="何日前に警告するか。カンマ区切り (既定 7,1)")
    s.set_defaults(fn=cmd_allday)

    s = sub.add_parser("rm", help="キーで削除。--id なら他のカレンダーの予定も")
    common(s, write=True)
    s.add_argument("key", nargs="?")
    s.add_argument("--id", help="生の eventId（`find` で得る）。自分で作った予定用")
    s.add_argument("--cal", help="その --id が載っている calendarId (既定 primary)")
    s.set_defaults(fn=cmd_rm)

    s = sub.add_parser("move", help="キーで日時変更。--id なら他のカレンダーの予定も")
    common(s, write=True)
    s.add_argument("key", nargs="?")
    s.add_argument("--id", help="生の eventId（`find` で得る）")
    s.add_argument("--cal", help="その --id が載っている calendarId (既定 primary)")
    s.add_argument("--date", required=True)
    s.add_argument("--start", help="HH:MM。終日予定に付けると時刻つきに変換される")
    s.add_argument("--end", help="HH:MM。--start と一緒に")
    s.add_argument("--dur", type=int, help="分。--start と一緒に (既定 60)")
    s.set_defaults(fn=cmd_move)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
