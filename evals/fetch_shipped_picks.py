"""Read-only: recover what past newsletters shipped, per section, from Firestore.

For every composed pipeline run, take the article links inside each SECTION block of the all-language
(1_1) and English-only (0_0) variants, and look the articles up in the run's stored pool by url. The
union per section is what agent3 picked for that run, before duplicate removal existed. Writes
data/shipped_picks.json (gitignored, may contain third-party text); nothing is written to Firestore.

CMD:  python -m evals.fetch_shipped_picks   (needs Application Default Credentials; GCP_PROJECT_ID or gcloud default)
"""
import html as html_lib
import json
import os
import re
from pathlib import Path

OUT_PATH = Path("data") / "shipped_picks.json"
VARIANTS = ("0_0", "1_1")
SKIP_SECTIONS = {"Research Spotlights"}
_SECTION = re.compile(r"<!-- SECTION:(.*?) -->(.*?)<!-- /SECTION:\1 -->", re.S)
_HREF = re.compile(r'<a href="([^"]+)"')


def section_urls(html):
    """{section: [urls in order of first appearance]} from one newsletter variant."""
    out = {}
    for name, body in _SECTION.findall(html or ""):
        if name in SKIP_SECTIONS:
            continue
        seen = []
        for u in _HREF.findall(body):
            u = html_lib.unescape(u)
            if u not in seen:
                seen.append(u)
        out[name] = seen
    return out


def picks_for_run(doc):
    """[{category, articles}] for one run doc: union of the variants' links matched against the run's pool."""
    pool = doc.get("news_summaries") or {}
    by_url = {a["url"]: a for arts in pool.values() if isinstance(arts, list) for a in arts if a.get("url")}
    union, missing = {}, 0
    for key in VARIANTS:
        for cat, urls in section_urls((doc.get("newsletter_variants") or {}).get(key)).items():
            bucket = union.setdefault(cat, [])
            for u in urls:
                if u in by_url and u not in [a["url"] for a in bucket]:
                    bucket.append(by_url[u])
                elif u not in by_url:
                    missing += 1
    return [{"category": c, "articles": a} for c, a in union.items() if a], missing


def main(limit=60):
    from google.cloud import firestore
    db = firestore.Client(project=os.environ.get("GCP_PROJECT_ID") or "ai-news-letter-497720")
    query = db.collection("pipeline_runs").order_by("started_at", direction=firestore.Query.DESCENDING).limit(limit)
    runs = []
    for d in query.stream():
        x = d.to_dict()
        if not x.get("newsletter_composed") or not x.get("newsletter_variants"):
            continue
        sections, missing = picks_for_run(x)
        runs.append({"run_id": d.id, "run_kind": x.get("run_kind", "release"), "sections": sections})
        print(f"{d.id[:16]}  {len(sections)} sections, {sum(len(s['articles']) for s in sections)} articles, "
              f"{missing} links not in pool (links, e.g. non-article)")
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps({"runs": runs}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
