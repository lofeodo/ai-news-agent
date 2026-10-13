"""Shapes for addresses and document ids that arrive in requests."""
import re

# One "@", no whitespace or "/", a dot inside the domain.
EMAIL_RE = re.compile(r"^[^@\s/\\]+@[^@\s/\\]+\.[^@\s/\\]+$")
MAX_EMAIL_LENGTH = 254

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")


def normalize_email(raw) -> str | None:
    """Trimmed, lowercased address, or None when it is not a plain address."""
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_RE.match(email):
        return None
    return email


def is_doc_id(value) -> bool:
    """True when `value` can be used as a Firestore document id without surprises."""
    if not isinstance(value, str) or not _DOC_ID_RE.match(value):
        return False
    if value in (".", "..") or (value.startswith("__") and value.endswith("__")):
        return False
    return True
