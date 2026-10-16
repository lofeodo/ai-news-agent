"""Render a sample custom newsletter that mixes topics, for a visual check of the block renderer.

Reads the committed fixtures (tests/fixtures/render/editions.json: a mixed definition, three editions, one custom
pool and the paper widget's data) and writes data/sample_mixed.html. No network, no Firestore, no Claude call.

    python tools/render_sample.py
    python tools/render_sample.py --french --out data/sample_fr.html
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))

import newsletter_def  # noqa: E402
import render  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "render" / "editions.json"
DEFAULT_OUT = ROOT / "data" / "sample_mixed.html"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--french", action="store_true", help="render for a reader who wants French articles too")
    args = parser.parse_args(argv)

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    os.environ.setdefault("MAILING_ADDRESS", "1 Example Street, Montreal QC")
    result = render.render_from_editions(
        newsletter_def.parse(data["definition"]), data["editions"], data["pools"], data["widget_data"],
        prefs={"include_french": args.french})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result.html, encoding="utf-8")
    print(f"wrote {args.out} ({result.size_bytes:,} bytes)")
    for w in result.warnings:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
