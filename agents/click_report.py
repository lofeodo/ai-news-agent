# agents/click_report.py
#
# Pure formatting of aggregate click counts for the weekly healthcheck email. No Firestore, no network.
# Inputs are the two aggregate docs (click_links: what shipped, click_counts: how often each was clicked)
# plus the number of emails delivered. Nothing per-person exists to report.
#
# Read it as a rough signal: the audience is small (under 50 readers at the time of writing), clicks are
# not unique per reader, mail scanners add noise (see the "early" and "bots" buckets), and the first item in
# a section gets more clicks than the last whatever its quality (position is not corrected for).

TOP_N = 3


def summarize(run_id: str, link_doc: dict, counts: dict, sent) -> dict:
    """Per-run figures. `counts` is {bucket: {url_key: n}}; `sent` is agent4's delivered count or None."""
    links = (link_doc or {}).get("links") or {}
    clicks = {k: n for k, n in (counts.get("clicks") or {}).items() if k in links and n > 0}
    by_category: dict[str, int] = {}
    for key, n in clicks.items():
        cat = links[key].get("category") or "Unknown"
        by_category[cat] = by_category.get(cat, 0) + n
    top = sorted(clicks.items(), key=lambda kv: (-kv[1], links[kv[0]].get("title", "")))[:TOP_N]
    total = sum(clicks.values())
    return {
        "run_id": run_id,
        "links": len(links),
        "total": total,
        "early": sum(n for k, n in (counts.get("early") or {}).items() if k in links),
        "bots": sum(n for k, n in (counts.get("bots") or {}).items() if k in links),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))),
        "top": [{"title": links[k].get("title", ""), "category": links[k].get("category", ""), "clicks": n}
                for k, n in top],
        "sent": sent if isinstance(sent, int) and sent > 0 else None,
    }


def _clicks(n: int) -> str:
    return f"{n} click" + ("" if n == 1 else "s")


def _line(s: dict) -> str:
    per = f" ({s['total'] / s['sent']:.2f} per delivered email, {s['sent']} delivered)" if s["sent"] else ""
    return (f"{_clicks(s['total'])}{per}; {s['early']} in the first minutes after sending (likely scanners), "
            f"{s['bots']} from automated user agents")


def format_section(current: dict | None, priors: list[dict]) -> str:
    """Email text. `current` is this week's summary (or None if click tracking was not on for it); `priors`
    are earlier runs, newest first, whose clicks have had time to arrive."""
    lines = ["Reader clicks (SendGrid click tracking; aggregate counts only):"]
    if current is None:
        lines.append("  - this week's newsletter: click tracking was not on for this run.")
    else:
        lines.append(f"  - this week's newsletter: {_clicks(current['total'])} so far (still arriving over the next days).")
    if not priors:
        lines.append("  - earlier weeks: no tracked runs yet.")
    for i, s in enumerate(priors):
        label = "last tracked week" if i == 0 else "earlier week"
        lines.append(f"  - {label} (run {s['run_id']}): {_line(s)}")
        if i == 0 and s["total"]:
            cats = ", ".join(f"{c} {n} ({n / s['total']:.0%})" for c, n in s["by_category"].items())
            lines.append(f"      by category: {cats}")
            tops = "; ".join(f'"{t["title"]}" ({t["clicks"]}, {t["category"]})' for t in s["top"])
            lines.append(f"      most clicked: {tops}")
    lines.append("  (Rough signal: small audience, clicks are not unique per reader, scanners add noise, and "
                 "position in the email is not corrected for. Informational only.)")
    return "\n".join(lines)
