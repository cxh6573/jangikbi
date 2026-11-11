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
POST_TITLES = os.environ.get("POST_TITLES", "1") == "1"  # 0이면 수집만, 게시는 Claude 예약 작업이 담당
TG_LIMIT = 3900  # 텔레그램 메시지 한도 4096자, 여유분 확보


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {url[:90]}… ({e})", file=sys.stderr)
            time.sleep(5 * (i + 1))
    return None


# 정오표·사설·철회 공지 제외 (제목이 이 단어로 시작하는 경우)
SKIP_TITLE = __import__("re").compile(r"^\s*(correction|corrigendum|erratum|errata|editorial|retraction|expression of concern)\b", __import__("re").I)


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
        if not title or SKIP_TITLE.match(title):
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


def license_of(w):
    """OpenAlex 작품에서 라이선스 문자열(cc-by 등)을 꺼낸다. 없으면 빈 문자열."""
    for loc in ("best_oa_location", "primary_location"):
        lic = (w.get(loc) or {}).get("license")
        if lic:
            return lic
    return ""


def from_openalex_author(author, since):
    if not OPENALEX_KEY or not author.get("openalex_id"):
        return []
    q = urllib.parse.urlencode({
        # from_created_date는 유료 플랜 전용 → 출판일 기준, 중복은 seen.json이 막음
        "filter": f"author.id:{author['openalex_id']},from_publication_date:{since}",
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
            "license": license_of(w),
            "via": "openalex",
        })
    return out


def enrich_abstracts(items):
    """Crossref에 초록이 없는 건은 OpenAlex에서 보충 (키가 있을 때만)."""
    if not OPENALEX_KEY:
        return
    need = [it for it in items if it.get("doi") and (not it.get("abstract") or "license" not in it)]
    for i in range(0, len(need), 40):
        chunk = need[i:i + 40]
        q = urllib.parse.urlencode({
            "filter": "doi:" + "|".join(it["doi"] for it in chunk),
            "per-page": 50,
            "select": "doi,abstract_inverted_index,open_access,best_oa_location,primary_location",
            "api_key": OPENALEX_KEY,
        })
        d = get_json(f"https://api.openalex.org/works?{q}")
        by = {(w.get("doi") or "").replace("https://doi.org/", "").lower(): w for w in (d or {}).get("results", [])}
        for it in chunk:
            w = by.get(it["doi"].lower())
            if w:
                if not it.get("abstract"):
                    it["abstract"] = inverted_to_text(w.get("abstract_inverted_index"))
                it["oa"] = (w.get("open_access") or {}).get("is_oa")
                it["license"] = license_of(w)
            elif "license" not in it:
                it["license"] = ""


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
    a_since = (now - timedelta(days=cfg.get("author_lookback_days", 60))).strftime("%Y-%m-%d")
    for a in cfg.get("authors", []):
        items += from_openalex_author(a, a_since)
        time.sleep(1)

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
        if POST_TITLES and not post_digest(new, now):
            sys.exit(1)  # 게시 실패 시 아무것도 저장 안 함 → 다음 실행 때 재시도
        y, w, _ = now.isocalendar()
        week_path = DATA / f"{y}-W{w:02d}.json"
        week = load(week_path, [])
        week += new
        week_path.write_text(json.dumps(week, ensure_ascii=False, indent=1), encoding="utf-8")
        seen |= ids
    # 일일 파일: 새 항목이 0건이어도 항상 기록 → Claude가 "신간 없음"과 "아직 미실행"을 구분
    daily = DATA / "daily"
    daily.mkdir(exist_ok=True)
    day_path = daily / f"{now:%Y-%m-%d}.json"
    day_items = load(day_path, {}).get("items", []) + new  # 하루 두 번 실행돼도 누적
    day_path.write_text(
        json.dumps({"date": f"{now:%Y-%m-%d}", "count": len(day_items), "items": day_items},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    seen_path.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")
    build_archive()


def build_archive():
    """'다시 읽기'용 목록: 지금까지 수집한 글 중 초록이 있는 것 전부(최신순), 라이선스 보충."""
    by_id = {}
    for wp in sorted(DATA.glob("20*-W*.json")):
        for it in load(wp, []):
            by_id[it["id"]] = it
    path = DATA / "archive.json"
    old = {it["id"]: it for it in load(path, [])}
    items = []
    for i, it in by_id.items():
        if "license" not in it and "license" in old.get(i, {}):
            it["license"] = old[i]["license"]
        if it.get("abstract"):
            items.append(it)
    missing = [it for it in items if "license" not in it][:120]  # 한 번에 최대 120건 보충
    if missing:
        enrich_abstracts(missing)
    for it in items:
        it.setdefault("license", "")
    items.sort(key=lambda x: x.get("found_at", ""), reverse=True)
    keep = ("id", "title", "authors", "source", "source_name", "url", "abstract", "oa", "license", "found_at")
    path.write_text(json.dumps([{k: it.get(k) for k in keep} for it in items], ensure_ascii=False, indent=1),
                    encoding="utf-8")


if __name__ == "__main__":
    main()
