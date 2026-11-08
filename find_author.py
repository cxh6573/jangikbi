"""OpenAlex 저자 ID 찾기: python find_author.py "Dan Goodley"
OPENALEX_API_KEY 환경변수 필요. 결과에서 소속·대표 분야를 보고 맞는 ID를 config.json에 넣는다."""
import json, os, sys, urllib.parse, urllib.request
name = " ".join(sys.argv[1:])
q = urllib.parse.urlencode({"search": name, "per-page": 5, "api_key": os.environ.get("OPENALEX_API_KEY", "")})
with urllib.request.urlopen(f"https://api.openalex.org/authors?{q}", timeout=30) as r:
    for a in json.load(r)["results"]:
        inst = ", ".join(i["display_name"] for i in (a.get("last_known_institutions") or [])[:1])
        topics = ", ".join(t["display_name"] for t in (a.get("topics") or [])[:3])
        print(a["id"].split("/")[-1], "|", a["display_name"], "|", a["works_count"], "편 |", inst, "|", topics)
