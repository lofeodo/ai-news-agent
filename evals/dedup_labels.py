"""Load the owner's dedup gold labels (evals/labels/dedup_cases_template.csv, filled in by hand).

`duplicate_group`: the same label on articles that are duplicates; blank = unique.
`note` == "?" marks an ambiguous article, excluded from the metrics.
"""
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from evals.make_dedup_cases import CASES_PATH, SHIPPED_CASES_PATH, SHIPPED_TEMPLATE_PATH, TEMPLATE_PATH


def _fix_id(raw):
    """Excel drops leading zeros from all-digit ids (051952554936 -> 51952554936); ids are 12 chars."""
    raw = raw.strip()
    return raw.zfill(12) if raw.isdigit() else raw


class LabelError(ValueError):
    pass


@dataclass
class Gold:
    groups: dict = field(default_factory=dict)      # case_id -> [frozenset(article_ids)]
    excluded: dict = field(default_factory=dict)    # case_id -> set(article_ids) marked "?"
    unlabeled_cases: list = field(default_factory=list)  # cases with no group label and no explicit sign-off


def load_dedup_gold(path=TEMPLATE_PATH, cases_path=CASES_PATH):
    """Validated gold. Raises LabelError on unknown ids or one-member groups.

    A case counts as labeled once any of its rows has a group or a note; a case with neither is reported in
    `unlabeled_cases` (the owner either hasn't reached it, or it has no duplicates and wasn't marked).
    """
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))["cases"]
    known = {c["case_id"]: {a["id"] for a in c["articles"]} for c in cases}
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    members, excluded, touched = {}, {}, set()
    for r in rows:
        cid, aid = r["case_id"], _fix_id(r["article_id"])
        if cid not in known:
            raise LabelError(f"unknown case {cid!r}")
        if aid not in known[cid]:
            raise LabelError(f"article {aid!r} is not in case {cid!r}")
        label = (r.get("duplicate_group") or "").strip()
        note = (r.get("note") or "").strip()
        if label or note:
            touched.add(cid)
        if note == "?":
            excluded.setdefault(cid, set()).add(aid)
            continue
        if label:
            members.setdefault((cid, label), set()).add(aid)

    gold = Gold(excluded=excluded)
    for (cid, label), ids in sorted(members.items()):
        if len(ids) < 2:
            raise LabelError(f"group {label!r} in case {cid!r} has only one article")
        gold.groups.setdefault(cid, []).append(frozenset(ids))
    gold.unlabeled_cases = sorted(set(known) - touched)
    return gold


def load_all_gold(sources=((TEMPLATE_PATH, CASES_PATH), (SHIPPED_TEMPLATE_PATH, SHIPPED_CASES_PATH))):
    """Gold from every labeled set (mined pool cases and shipped-section cases), merged; case ids don't collide."""
    merged = Gold()
    for labels, cases in sources:
        g = load_dedup_gold(labels, cases)
        for cid in g.groups.keys() & merged.groups.keys():
            raise LabelError(f"case id {cid!r} appears in two label sets")
        merged.groups.update(g.groups)
        merged.excluded.update(g.excluded)
        merged.unlabeled_cases += g.unlabeled_cases
    return merged
