import pytest

from agents import ids


@pytest.mark.parametrize("raw,expected", [
    ("a@b.com", "a@b.com"),
    ("  A@B.Com ", "a@b.com"),
    ("a/b@x.com", None),
    ("a@x/y.com", None),
    ("a b@x.com", None),
    ("a@@x.com", None),
    ("a@x", None),
    ("", None),
    (None, None),
    ("a@" + "b" * 250 + ".com", None),
])
def test_normalize_email(raw, expected):
    assert ids.normalize_email(raw) == expected


@pytest.mark.parametrize("value,ok", [
    ("abc123_-", True),
    ("2026-10-12T10.00", True),
    ("a/b", False),
    (".", False),
    ("..", False),
    ("__name__", False),
    ("", False),
    ("x" * 129, False),
    (None, False),
])
def test_is_doc_id(value, ok):
    assert ids.is_doc_id(value) is ok
