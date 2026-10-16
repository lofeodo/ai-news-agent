"""Regenerate the render golden files: python tests/make_render_goldens.py

Not collected by pytest (the name does not start with test_). It writes the fixture input
(tests/fixtures/render/input.json) and today's compose_html output for the AI newsletter (all four variants)
and every other topic (tests/fixtures/render/<topic>_<variant>.html). tests/test_render_goldens.py renders the
same input and compares byte for byte, so any change to the email's HTML shows up as a failing test.

Only regenerate when an email change is intended, and look at the diff of the .html files before committing.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agents"))

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "render")
INPUT_PATH = os.path.join(FIXTURE_DIR, "input.json")
MAILING_ADDRESS = "1 Example Street, Montreal QC H2X 1Y4"
TOPIC_IDS = ["ai", "finance", "gaming", "boardgames", "printing3d"]


def _article(topic_id: str, section: str, i: int, french: bool = False) -> dict:
    """One summarized article. The index picks a variant so every card shape appears somewhere."""
    kind = i % 5
    a = {
        "title": f"{section} story {i} for {topic_id}",
        "url": f"https://news.example/{topic_id}/{section.split()[0].lower()}/{i}",
        "summary": f"What happened in {section} ({i}), and why it matters to readers of {topic_id}.",
        "description": "",
        "source": ["hackernews", "newsapi", "rss"][i % 3],
        "language": "fr" if french else "en",
        "hn_score": [412, None, 0, 57, None][kind],
    }
    if kind == 1:
        a["title"] = f"Quotes \"&\" <b>tags</b> in {section} {{{{UNSUBSCRIBE_URL}}}}"
    if kind == 2:
        a["summary"] = ""
        a["description"] = f"Only a description for {section} {i}."
        a["used_fallback"] = True
    if kind == 4:
        a["url"] = "javascript:alert(1)"
    if french:
        a["title"] = f"Nouvelle {i} en {section}"
        a["summary"] = f"Résumé en français {i} — {section}."
    return a


def build_input() -> dict:
    import topics

    data = {
        "week_of": "October 19, 2026",
        "intro": "A busy week: <three> big launches & a {{PREFERENCES_URL}} test. " * 4,
        "papers": [{
            "title": "Scaling <Sparse> Experts & Friends",
            "authors": ["Ada Lovelace", "Alan Turing", "Grace Hopper", "Edsger Dijkstra"],
            "summary": "## Heading the model added\nA hook about the paper, in two short sentences. It matters.",
            "hf_url": "https://huggingface.co/papers/2610.00001",
            "pdf_url": "https://arxiv.org/pdf/2610.00001",
            "scores": {"total": 37, "upvotes": 1234},
        }],
        "topics": {},
    }
    for tid in TOPIC_IDS:
        topic = topics.get_topic(tid)
        selected_all, selected_en = {}, {}
        for n, cat in enumerate(topic.categories):
            count = (n + 2) % 5 if n != 2 else 0   # third section empty in every topic
            arts = [_article(tid, cat.name, i) for i in range(min(count, cat.cap))]
            if "fr" in topic.languages and n in (0, 4):
                selected_all[cat.name] = arts[:-1] + [_article(tid, cat.name, 9, french=True)]
                selected_en[cat.name] = arts
            else:
                selected_all[cat.name] = arts
                selected_en[cat.name] = arts
        data["topics"][tid] = {"selected_all": selected_all, "selected_en": selected_en}
    return data


def render_all(data: dict) -> dict[str, str]:
    """{file name: html} for every topic and variant, with the fixed mailing address."""
    import agent3_compose as a3
    import topics

    os.environ["MAILING_ADDRESS"] = MAILING_ADDRESS
    out = {}
    for tid in TOPIC_IDS:
        topic = topics.get_topic(tid)
        sel = data["topics"][tid]
        for key, prefs in topic.variant_prefs().items():
            selection = sel["selected_all"] if prefs["include_french"] else sel["selected_en"]
            papers = data["papers"] if topic.spotlight == "papers" else []
            out[f"{tid}_{key}.html"] = a3.compose_html(
                data["intro"], papers, selection, data["week_of"],
                include_canada=prefs["include_canada"], topic=topic)
    return out


def main() -> None:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    data = build_input()
    with open(INPUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    for name, html in render_all(data).items():
        with open(os.path.join(FIXTURE_DIR, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print(f"wrote {name} ({len(html.encode('utf-8')):,} bytes)")


if __name__ == "__main__":
    main()
