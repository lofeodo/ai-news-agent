# agents/agent4_send.py
#
# Reads the composed newsletter HTML from Firestore (cloud) or local file (local),
# then sends it via SendGrid.
#
# Cloud mode: queries the `subscribers` collection for active subscribers and
# sends a personalized copy to each one (per-subscriber unsubscribe + preferences
# links substituted into the footer placeholders embedded by agent3).
#
# Local mode: reads data/newsletter.html and sends a single copy to
# TEST_RECIPIENT_EMAIL (if set). Local mode does NOT query Firestore.
#
# Triggered by a Cloud Scheduler job at 7:00 AM every Monday — separate from the
# pipeline orchestrator, which runs at 6:00 AM.

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    DATA_DIR,
    GCP_PROJECT_ID,
    USE_FIRESTORE,
    FIRESTORE_COLLECTION,
    SUBSCRIBERS_COLLECTION,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME      = "Latent SpaceMail"
SENDER_EMAIL         = "newsletter@lofeodo.com"
SENDER_NAME          = "Latent SpaceMail"
SENDGRID_SECRET_NAME = "sendgrid-api-key"
USE_SECRET_MANAGER   = os.environ.get("USE_SECRET_MANAGER", "false").lower() == "true"

# Public base URL of the agent-subscriptions service — used to build each
# subscriber's one-click unsubscribe link. Must match the value set on the
# agent-subscriptions service.
SERVICE_BASE_URL  = os.environ.get("SERVICE_BASE_URL", "").rstrip("/")

# Base URL of the Firebase Hosting frontend — used for the preferences magic
# link. Unset until the frontend exists (Phase 11); until then the prefs link
# is malformed, but we still substitute the placeholder so the raw
# "{{PREFERENCES_URL}}" string never ships in an email.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "").rstrip("/")

# Local-mode only: send a single copy here. No effect in cloud mode.
TEST_RECIPIENT_EMAIL = os.environ.get("TEST_RECIPIENT_EMAIL", "")

# Cloud-mode test override: if set, skip the Firestore subscriber list and
# send only to this address. Used for test-send jobs that must not reach
# real subscribers.
TEST_SEND_TO = os.environ.get("TEST_SEND_TO", "")

# Keep in sync with DEFAULT_SECTIONS in agents/agent_subscriptions.py
_DEFAULT_SECTIONS = [
    "Model & Product Releases",
    "Industry & Business",
    "Policy, Law & Regulation",
    "Open Source & Tools",
    "Safety & Alignment",
    "Society & Culture",
    "Research Spotlights",
]
_DEFAULT_SECTIONS_SET = set(_DEFAULT_SECTIONS)

# Regex to extract <!-- SECTION:Name -->...<!-- /SECTION:Name --> blocks
_SECTION_RE = re.compile(
    r'<!-- SECTION:(.+?) -->(.*?)<!-- /SECTION:\1 -->',
    re.DOTALL,
)

# Matches the section-number span in section strips: <span style="...font-size:28px...letter-spacing:-1px...">01</span>
_SECTION_NUM_RE = re.compile(
    r'(<span\s+style="[^"]*?font-size:28px[^"]*?letter-spacing:-1px[^"]*?">\s*)\d{1,2}(\s*</span>)',
)

# Matches the <!-- TOC -->...<!-- /TOC --> wrapper around the inner TOC table
_TOC_RE = re.compile(r'<!-- TOC -->(.*?)<!-- /TOC -->', re.DOTALL)

_TOC_F     = "'Menlo','Cascadia Mono','Consolas','Courier New',monospace"
_TOC_AMBER = "#c8b89a"
_TOC_GRAY  = "#585858"


# ---------------------------------------------------------------------------
# SendGrid
# ---------------------------------------------------------------------------

def _get_sendgrid_api_key() -> str:
    """Load SendGrid API key from Secret Manager (cloud) or env var (local)."""
    if USE_SECRET_MANAGER:
        from google.cloud import secretmanager
        client   = secretmanager.SecretManagerServiceClient()
        name     = f"projects/{GCP_PROJECT_ID}/secrets/{SENDGRID_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    else:
        key = os.environ.get("SENDGRID_API_KEY", "")
        if not key:
            raise RuntimeError("SENDGRID_API_KEY env var not set for local mode")
        return key


def send_email(api_key: str, to_email: str, html_body: str, subject: str) -> None:
    """Send one email. Raises on non-2xx (urllib raises on 4xx/5xx)."""
    import urllib.request
    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email}]}],
        "from":    {"email": SENDER_EMAIL, "name": SENDER_NAME},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(f"[sendgrid]  sent — status {resp.status}", flush=True)


# ---------------------------------------------------------------------------
# Personalization
# ---------------------------------------------------------------------------

def _personalize(html: str, token: str) -> str:
    """Substitute the per-subscriber footer placeholders embedded by agent3.

    Both placeholders must be replaced (option-A contract). The preferences
    link depends on FRONTEND_BASE_URL — malformed until the frontend exists,
    but we still substitute so the raw placeholder never ships.
    """
    unsub_link = f"{SERVICE_BASE_URL}/unsubscribe?token={token}"
    prefs_link = f"{FRONTEND_BASE_URL}/preferences.html?token={token}"
    html = html.replace("{{UNSUBSCRIBE_URL}}", unsub_link)
    html = html.replace("{{PREFERENCES_URL}}", prefs_link)
    return html


# ---------------------------------------------------------------------------
# Newsletter lookup
# ---------------------------------------------------------------------------

def _variant_key(prefs: dict) -> str:
    """Map a subscriber prefs dict to its newsletter variant key."""
    fr = "1" if prefs.get("include_french", False) else "0"
    ca = "1" if prefs.get("include_canada", False) else "0"
    return f"{fr}_{ca}"


def _normalize_image_urls(html: str) -> str:
    """Fix stale image base URLs from old pipeline runs that used FRONTEND_BASE_URL as the image root."""
    html = html.replace(
        "https://latentspacemail.web.app/newsletter/images/",
        "https://newsletter.lofeodo.com/images/",
    )
    html = html.replace(
        "https://newsletter.lofeodo.com/newsletter/images/",
        "https://newsletter.lofeodo.com/images/",
    )
    return html


# ---------------------------------------------------------------------------
# Per-subscriber section customization
# ---------------------------------------------------------------------------

def _renumber_section(section_html: str, new_num: str) -> str:
    """Replace the 2-digit section number in a section strip (font-size:28px span)."""
    return _SECTION_NUM_RE.sub(rf'\g<1>{new_num}\2', section_html)


def _short_label(name: str) -> str:
    """Mirror agent3's TOC abbreviation logic: split on ' &' or ',' and uppercase."""
    if name == "Research Spotlights":
        return "RESEARCH"
    return name.split(" &")[0].split(",")[0].upper()


def _toc_cell(num: str, short: str) -> str:
    return (
        f'<td class="mob-toc-cell" style="width:25%;padding:0 0 6px 0;">'
        f'<span style="font-family:{_TOC_F};font-size:11px;color:{_TOC_AMBER};font-weight:700;">{num}</span>'
        f'<span style="font-family:{_TOC_F};font-size:11px;color:{_TOC_GRAY};">&thinsp;{short}</span>'
        f'</td>'
    )


def _build_toc_html(entries: list) -> str:
    """Build the inner TOC table rows from a list of (display_num, section_name) pairs."""
    cells = [(_toc_cell(num, _short_label(name))) for num, name in entries]
    # Pad to a multiple of 4 with empty cells
    while len(cells) % 4 != 0:
        cells.append(_toc_cell("", ""))
    row1 = "".join(cells[:4])
    row2 = "".join(cells[4:])
    return f'<tr>{row1}</tr>\n<tr>{row2}</tr>'


def _load_user_section_configs(db, subscribers: list) -> dict:
    """Batch-load section_config from users/{uid} for all subscribers with a uid.

    Returns uid -> section_config dict. Subscribers without a uid are excluded
    and will receive the default full newsletter for their variant.
    """
    uids = [s["uid"] for s in subscribers if s.get("uid")]
    if not uids:
        return {}
    refs = [db.collection("users").document(uid) for uid in uids]
    result = {}
    for snap in db.get_all(refs):
        if snap.exists:
            cfg = snap.to_dict().get("section_config")
            if cfg:
                result[snap.id] = cfg
    return result


def _apply_section_config(html: str, section_config: dict | None) -> str:
    """Filter and reorder newsletter sections based on a premium subscriber's section_config.

    Returns unchanged html when:
    - section_config is None/empty
    - enabled_sections is null (meaning all-on in default order)
    - html has no section markers (old newsletter, backward-compatible fallback)
    """
    if not section_config:
        return html

    enabled = section_config.get("enabled_sections")
    if enabled is None:
        return html  # null = all sections in default order, nothing to do

    # Validate and filter to known DEFAULT_SECTIONS only
    desired = [s for s in enabled if s in _DEFAULT_SECTIONS_SET]

    # Graceful fallback for newsletters generated before section markers were added
    if not _SECTION_RE.search(html):
        return html

    # Extract all section blocks from HTML
    sections = {m.group(1): m.group(0) for m in _SECTION_RE.finditer(html)}

    # Build reordered/filtered section HTML
    parts       = []
    toc_entries = []
    counter     = 1

    for name in desired:
        if name == "Research Spotlights":
            continue  # always placed last
        if name not in sections:
            continue
        new_num = f"{counter:02d}"
        parts.append(_renumber_section(sections[name], new_num))
        toc_entries.append((new_num, name))
        counter += 1

    # Canada & Montreal passes through unchanged — it is controlled by the
    # include_canada pref, not section_config. Append after enabled news sections.
    canada = sections.get("Canada & Montreal")
    if canada:
        canada_num = f"{counter:02d}"
        parts.append(_renumber_section(canada, canada_num))
        toc_entries.append((canada_num, "Canada & Montreal"))
        counter += 1

    # Research Spotlights: always last if the user has it enabled
    if "Research Spotlights" in desired and "Research Spotlights" in sections:
        parts.append(sections["Research Spotlights"])
        toc_entries.append(("RES", "Research Spotlights"))

    # Replace the entire section zone (first marker to last marker)
    all_matches = list(_SECTION_RE.finditer(html))
    zone_start  = all_matches[0].start()
    zone_end    = all_matches[-1].end()
    html = html[:zone_start] + "\n".join(parts) + html[zone_end:]

    # Rebuild TOC to keep section numbers consistent with the reordered body
    new_toc_rows = _build_toc_html(toc_entries)
    toc_replacement = (
        f'<!-- TOC -->\n'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'{new_toc_rows}\n'
        f'</table>\n'
        f'<!-- /TOC -->'
    )
    html = _TOC_RE.sub(toc_replacement, html, count=1)

    return html


def _load_latest_newsletter(db):
    """Return (variants, subject) for the most recent run with newsletter_html set.

    variants is a dict keyed by "0_0" / "1_0" / "0_1" / "1_1".
    Falls back to {"0_0": newsletter_html} for old runs that predate variants.
    """
    from google.cloud import firestore as _fs
    # Most recent run doc where agent3 finished saving all variants.
    # Agent4 is triggered by Scheduler independently — it doesn't receive a run_id.
    results = (
        db.collection(FIRESTORE_COLLECTION)
        .where("newsletter_composed", "==", True)
        .order_by("started_at", direction=_fs.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    docs = list(results)
    if not docs:
        raise RuntimeError("[agent4]  No Firestore document found with newsletter_html set")
    data     = docs[0].to_dict()
    variants = data.get("newsletter_variants")
    if not variants:
        variants = {"0_0": data.get("newsletter_html", "")}
    variants = {k: _normalize_image_urls(v) for k, v in variants.items()}
    subject  = data.get("newsletter_subject") or f"{NEWSLETTER_NAME} — {datetime.now().strftime('%B %d, %Y')}"
    print(f"[agent4]  Loaded newsletter variants from Firestore "
          f"(run_id={data.get('run_id')}, keys={list(variants.keys())})", flush=True)
    return variants, subject


def _active_subscribers(db) -> list[dict]:
    """Return a list of active subscriber dicts (must include email + token)."""
    docs = (
        db.collection(SUBSCRIBERS_COLLECTION)
        .where("active", "==", True)
        .stream()
    )
    subs = [d.to_dict() for d in docs]
    return subs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(run_id: str):
    """Read newsletter HTML and send via SendGrid.

    Cloud mode: per-subscriber send loop over active subscribers.
    Local mode: single send to TEST_RECIPIENT_EMAIL (no Firestore).
    Always exits without raising on per-send failures; logs a structured
    send_summary line for Cloud Logging.
    """
    start_time = datetime.now()

    # -------------------------------------------------------------------
    # LOCAL MODE — single send to TEST_RECIPIENT_EMAIL, no Firestore.
    # -------------------------------------------------------------------
    if not USE_FIRESTORE:
        variant_path = os.path.join(DATA_DIR, "newsletter_0_0.html")
        legacy_path  = os.path.join(DATA_DIR, "newsletter.html")
        html_path    = variant_path if os.path.exists(variant_path) else legacy_path
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        subject = f"{NEWSLETTER_NAME} — {datetime.now().strftime('%B %d, %Y')}"
        print(f"[agent4]  Loaded newsletter HTML from {html_path}", flush=True)

        if not TEST_RECIPIENT_EMAIL:
            print("  [warn]  TEST_RECIPIENT_EMAIL not set — skipping send (local mode)", flush=True)
            return

        # In local mode there is no subscriber token; substitute empty-ish
        # links so the placeholders don't ship raw. (Local test only.)
        api_key = _get_sendgrid_api_key()
        local_html = _personalize(html, token="LOCAL-TEST")
        try:
            send_email(api_key, TEST_RECIPIENT_EMAIL, local_html, subject)
            print("[agent4]  local test send OK", flush=True)
        except Exception as e:
            print(f"[agent4]  local test send FAILED: {e}", flush=True)
        return

    # -------------------------------------------------------------------
    # CLOUD MODE — per-subscriber send loop.
    # -------------------------------------------------------------------
    from google.cloud import firestore as _fs
    db = _fs.Client(project=GCP_PROJECT_ID)

    variants, subject = _load_latest_newsletter(db)

    if TEST_SEND_TO:
        print(f"[agent4]  TEST_SEND_TO override — sending only to {TEST_SEND_TO}", flush=True)
        subscribers = [{"email": TEST_SEND_TO, "token": "TEST-SEND"}]
    else:
        subscribers = _active_subscribers(db)
    print(f"[agent4]  {len(subscribers)} active subscriber(s)", flush=True)

    # Batch-load section configs for premium subscribers (those with a uid).
    # Legacy/token-only subscribers have uid=None and skip this step.
    uid_section_configs: dict = {}
    if not TEST_SEND_TO:
        try:
            uid_section_configs = _load_user_section_configs(db, subscribers)
            print(f"[agent4]  Section configs loaded for {len(uid_section_configs)} user(s)", flush=True)
        except Exception as e:
            print(f"  [warn]  failed to load section configs: {e} — using defaults", flush=True)

    api_key = _get_sendgrid_api_key()

    sent     = 0
    failed   = 0
    failures = []  # list of {email, error}

    for sub in subscribers:
        email = sub.get("email")
        token = sub.get("token")
        if not email or not token:
            # Defensive: a malformed doc shouldn't exist, but never let it
            # abort the loop. Skip and record.
            failed += 1
            failures.append({"email": email or "<missing>", "error": "missing email or token"})
            print(f"  [warn]  skipping malformed subscriber doc: {sub}", flush=True)
            continue

        prefs = sub.get("prefs", {})
        key   = _variant_key(prefs)
        html  = variants.get(key) or variants.get("0_0", "")
        if not html:
            failed += 1
            failures.append({"email": email, "error": f"no HTML for variant {key}"})
            print(f"  [warn]  no HTML for variant {key}, skipping {email}", flush=True)
            continue

        # Apply per-subscriber section customization for premium accounts
        uid = sub.get("uid")
        if uid and uid in uid_section_configs:
            try:
                html = _apply_section_config(html, uid_section_configs[uid])
            except Exception as e:
                print(f"  [warn]  section config failed for {email}: {e} — using default", flush=True)

        personalized = _personalize(html, token)
        try:
            send_email(api_key, email, personalized, subject)
            sent += 1
        except Exception as e:
            failed += 1
            failures.append({"email": email, "error": str(e)})
            print(f"  [error]  send failed: {e}", flush=True)
            # Continue — one bad send must not abort the rest.

    elapsed = (datetime.now() - start_time).total_seconds()

    # Structured summary line — Cloud Logging parses stdout JSON into
    # jsonPayload, making send history queryable:
    #   gcloud logging read 'jsonPayload.event="send_summary"' ...
    summary = {
        "event":     "send_summary",
        "service":   "agent4",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total":     len(subscribers),
        "sent":      sent,
        "failed":    failed,
        "failures":  failures,
        "subject":   subject,
        "elapsed_s": round(elapsed, 1),
    }
    print(json.dumps(summary), flush=True)

    # Human-readable tail for the live log view.
    print(f"\n--- agent4 done in {elapsed:.1f}s — sent {sent}, failed {failed} ---", flush=True)
    # Always exit 0 (no raise): a nonzero exit would just create Scheduler
    # noise with no useful retry behavior. Failures are logged loudly above.


if __name__ == "__main__":
    run(run_id="local-debug")