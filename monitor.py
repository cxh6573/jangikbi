"""장애학 읽는 비장애인 — 신간 모니터 (매일 실행)

1) Crossref에서 지정 학술지의 최근 논문을, OpenAlex에서 지정 학자의 최근 글을 가져온다.
2) 이미 올린 것(data/seen.json)은 건너뛴다.
3) 새 제목을 하루치 메시지로 묶어 텔레그램 채널에 올린다 (AI 사용 없음).
4) 같은 목록을 주간 파일(data/YYYY-Www.json)에 쌓는다 → 주 1회 Claude 예약 작업이 읽어 요약.

환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (없으면 화면 출력만), OPENALEX_API_KEY (선택)
표준 라이브러리만 사용.
"""
import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
MAILTO = os.environ.get("CONTACT_EMAIL", "")
UA = "jangeup-monitor/1.0" + (f" (mailto:{MAILTO})" if MAILTO else "")
OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_LIMIT = 3900  # 텔레그램 메시지 한도 4096자, 여유분 확보


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {url[:90]}… ({e})", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


def clean(s):
    return " ".join(html.unescape(html.unescape(s or "")).replace("<jats:p>", "").replace("</jats:p>", "").split())


def strip_tags(s):
    import re
    return clean(re.sub(r"<[^>]+>", " ", s or ""))


def from_crossref(journal, since):
    q = urllib.parse.urlencode({
        "filter": f"from-created-date:{since},type:journal-article",
        "rows": 100,
        "select": "DOI,title,author,abstract,created,URL",
        **({"mailto": MAILTO} if MAILTO else {}),
    })
    d = get_json(f"https://api.crossref.org/journals/{journal['issn']}/works?{q}")
    out = []
    for w in (d or {}).get("message", {}).get("items", []):
        title = clean((w.get("title") or [""])[0])
        if not title:
            continue
        authors = ", ".join(
            " ".join(x for x in (a.get("given"), a.get("family")) if x)
            for a in (w.get("author") or [])[:4]
        )
        out.append({
            "id": "doi:" + w["DOI"].lower(),
            "doi": w["DOI"],
            "title": title,
            "authors": authors,
            "source": journal["short"],
            "source_name": journal["name"],
            "url": f"https://doi.org/{w['DOI']}",
            "abstract": strip_tags(w.get("abstract")),
            "via": "crossref",
        })
    return out


def inverted_to_text(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def from_openalex_author(author, since):
    if not OPENALEX_KEY or not author.get("openalex_id"):
        return []
    q = urllib.parse.urlencode({
        "filter": f"author.id:{author['openalex_id']},from_created_date:{since}",
        "per-page": 50,
        "api_key": OPENALEX_KEY,
    })
    d = get_json(f"https://api.openalex.org/works?{q}")
    out = []
    for w in (d or {}).get("results", []):
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "기타"
        out.append({
            "id": ("doi:" + doi.lower()) if doi else w["id"],
            "doi": doi,
            "title": clean(w.get("title")),
            "authors": ", ".join(a["author"]["display_name"] for a in w.get("authorships", [])[:4]),
            "source": "학자:" + author["name"],
            "source_name": src,
            "url": f"https://doi.org/{doi}" if doi else w["id"],
            "abstract": inverted_to_text(w.get("abstract_inverted_index")),
            "oa": (w.get("open_access") or {}).get("is_oa"),
            "via": "openalex",
        })
    return out


def enrich_abstracts(items):
    """Crossref에 초록이 없는 건은 OpenAlex에서 보충 (키가 있을 때만)."""
    if not OPENALEX_KEY:
        return
    need = [it for it in items if it.get("doi") and not it.get("abstract")]
    for i in range(0, len(need), 40):
        chunk = need[i:i + 40]
        q = urllib.parse.urlencode({
            "filter": "doi:" + "|".join(it["doi"] for it in chunk),
            "per-page": 50,
            "select": "doi,abstract_inverted_index,open_access",
            "api_key": OPENALEX_KEY,
        })
        d = get_json(f"https://api.openalex.org/works?{q}")
        by = {(w.get("doi") or "").replace("https://doi.org/", "").lower(): w for w in (d or {}).get("results", [])}
        for it in chunk:
            w = by.get(it["doi"].lower())
            if w:
                it["abstract"] = inverted_to_text(w.get("abstract_inverted_index"))
                it["oa"] = (w.get("open_access") or {}).get("is_oa")


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        print(text, "\n" + "-" * 40)
        return True
    body = urllib.parse.urlencode({
        "chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("ok", False)
    except Exception as e:  # noqa: BLE001
        print(f"[error] telegram: {e}", file=sys.stderr)
        return False


def post_digest(items, today):
    head = f"📚 <b>{today:%m/%d} 신간 ({len(items)}건)</b>\n"
    lines = [
        f"[{html.escape(it['source'])}] <a href=\"{html.escape(it['url'])}\">{html.escape(it['title'])}</a>"
        for it in items
    ]
    msgs, cur = [], head
    for ln in lines:
        if len(cur) + len(ln) + 1 > TG_LIMIT:
            msgs.append(cur)
            cur = f"📚 <b>{today:%m/%d} 신간 (계속)</b>\n"
        cur += ln + "\n"
    msgs.append(cur)
    ok = True
    for m in msgs:
        ok = tg_send(m) and ok
        time.sleep(1)
    return ok


def main():
    cfg = load(ROOT / "config.json", {})
    now = datetime.now(KST)
    since = (now - timedelta(days=cfg.get("lookback_days", 7))).strftime("%Y-%m-%d")
    DATA.mkdir(exist_ok=True)
    seen_path = DATA / "seen.json"
    seen = set(load(seen_path, []))

    items = []
    for j in cfg.get("journals", []):
        items += from_crossref(j, since)
        time.sleep(1)
    for a in cfg.get("authors", []):
        items += from_openalex_author(a, since)

    new, ids = [], set()
    for it in items:
        if it["id"] in seen or it["id"] in ids:
            continue
        ids.add(it["id"])
        new.append(it)
    print(f"새 항목 {len(new)}건 (조회 {len(items)}건)")

    if new:
        enrich_abstracts(new)
        stamp = now.isoformat(timespec="minutes")
        for it in new:
            it["found_at"] = stamp
        if not post_digest(new, now):
            sys.exit(1)  # 게시 실패 시 아무것도 저장 안 함 → 다음 실행 때 재시도
        y, w, _ = now.isocalendar()
        week_path = DATA / f"{y}-W{w:02d}.json"
        week = load(week_path, [])
        week += new
        week_path.write_text(json.dumps(week, ensure_ascii=False, indent=1), encoding="utf-8")
        seen |= ids
    seen_path.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
