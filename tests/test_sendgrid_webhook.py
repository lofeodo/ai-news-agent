"""SendGrid webhook signature check and event reduction. Keys are generated here; no network."""
import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

import sendgrid_webhook as sw

NOW = 1_800_000_000


@pytest.fixture(scope="module")
def keys():
    private = ec.generate_private_key(ec.SECP256R1())
    der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(der).decode()


def sign(private, timestamp, body):
    sig = private.sign(timestamp.encode() + body, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(sig).decode()


def test_valid_signature_passes(keys):
    private, pub = keys
    body = b'[{"event":"click"}]'
    sw.verify_signature(pub, sign(private, str(NOW), body), str(NOW), body, now=NOW)


def test_body_tampering_is_rejected(keys):
    private, pub = keys
    sig = sign(private, str(NOW), b'[{"event":"click"}]')
    with pytest.raises(sw.SignatureError, match="does not match"):
        sw.verify_signature(pub, sig, str(NOW), b'[{"event":"click","x":1}]', now=NOW)


def test_timestamp_is_part_of_the_signed_payload(keys):
    private, pub = keys
    body = b"[]"
    sig = sign(private, str(NOW), body)
    with pytest.raises(sw.SignatureError):
        sw.verify_signature(pub, sig, str(NOW + 1), body, now=NOW)


def test_wrong_key_is_rejected(keys):
    private, pub = keys
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(sw.SignatureError, match="does not match"):
        sw.verify_signature(pub, sign(other, str(NOW), b"[]"), str(NOW), b"[]", now=NOW)


@pytest.mark.parametrize("offset", [601, -601, 86_400])
def test_stale_or_future_timestamp_is_rejected_even_if_validly_signed(keys, offset):
    private, pub = keys
    ts = str(NOW - offset)
    with pytest.raises(sw.SignatureError, match="window"):
        sw.verify_signature(pub, sign(private, ts, b"[]"), ts, b"[]", now=NOW)


def test_edge_of_window_passes(keys):
    private, pub = keys
    ts = str(NOW - 600)
    sw.verify_signature(pub, sign(private, ts, b"[]"), ts, b"[]", now=NOW)


@pytest.mark.parametrize("sig,ts", [(None, "1"), ("abc", None), ("", "1"), ("abc", "")])
def test_missing_headers(keys, sig, ts):
    with pytest.raises(sw.SignatureError, match="missing"):
        sw.verify_signature(keys[1], sig, ts, b"[]", now=NOW)


def test_non_numeric_timestamp(keys):
    with pytest.raises(sw.SignatureError, match="integer"):
        sw.verify_signature(keys[1], "abc", "yesterday", b"[]", now=NOW)


def test_garbage_signature_and_garbage_key_do_not_raise_anything_else(keys):
    private, pub = keys
    with pytest.raises(sw.SignatureError):
        sw.verify_signature(pub, "!!!not base64!!!", str(NOW), b"[]", now=NOW)
    with pytest.raises(sw.SignatureError):
        sw.verify_signature(pub, base64.b64encode(b"short").decode(), str(NOW), b"[]", now=NOW)
    with pytest.raises(sw.SignatureError):
        sw.verify_signature("not a key", sign(private, str(NOW), b"[]"), str(NOW), b"[]", now=NOW)


def test_non_ec_key_is_refused(keys):
    private, _ = keys
    rsa_der = rsa.generate_private_key(65537, 2048).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(sw.SignatureError, match="elliptic"):
        sw.verify_signature(base64.b64encode(rsa_der).decode(), sign(private, str(NOW), b"[]"), str(NOW), b"[]", now=NOW)


# --- event reduction -------------------------------------------------------------------------

PII = {"email": "reader@example.com", "ip": "203.0.113.7", "sg_message_id": "msgid.filter0001",
       "sg_event_id": "evt-abc123", "useragent": "Mozilla/5.0 (X11) Firefox/130"}


def click(**over):
    ev = {"event": "click", "url": "https://example.com/a", "run_id": "2026-10-12T100004Z", "timestamp": NOW,
          "category": ["newsletter"], **PII}
    ev.update(over)
    return ev


def reduce(events):
    return sw.reduce_events(json.dumps(events).encode())


def test_click_is_reduced_to_exactly_four_fields():
    assert reduce([click()]) == [{"run_id": "2026-10-12T100004Z", "url": "https://example.com/a",
                                  "timestamp": NOW, "bot": False}]


def test_no_subscriber_data_survives_reduction():
    out = json.dumps(reduce([click(), click(useragent="Googlebot/2.1")]))
    for secret in ("reader@example.com", "203.0.113.7", "msgid", "evt-abc123", "Firefox", "Googlebot"):
        assert secret not in out


def test_non_click_events_and_foreign_mail_are_ignored():
    evs = [click(event="delivered"), click(event="open"), click(event="unsubscribe"), click(event="bounce"),
           click(run_id=None), {k: v for k, v in click().items() if k != "run_id"}, click(run_id=""),
           click(run_id=123), click(url=None), click(url=""), "junk", 7, None, ["x"]]
    assert reduce(evs) == []


def test_oversized_fields_are_dropped():
    assert reduce([click(url="https://e.com/" + "a" * 3000), click(run_id="r" * 200)]) == []


def test_bot_user_agents_are_flagged_not_dropped():
    out = reduce([click(useragent=ua) for ua in ("Googlebot/2.1", "Mozilla/5.0 ProofpointURLDefense", "python-requests/2.31",
                                                  "Mozilla/5.0 Chrome/120", None)])
    assert [e["bot"] for e in out] == [True, True, True, False, False]


def test_timestamp_must_be_a_real_integer():
    out = reduce([click(timestamp="soon"), click(timestamp=True), click(timestamp=None), click(timestamp=5)])
    assert [e["timestamp"] for e in out] == [None, None, None, 5]


def test_body_must_be_a_json_array():
    with pytest.raises(ValueError):
        sw.reduce_events(b'{"event":"click"}')
    with pytest.raises(ValueError):
        sw.reduce_events(b"not json")
