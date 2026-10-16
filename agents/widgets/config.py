# agents/widgets/config.py
#
# Validation of a widget's reader- or profile-supplied config against its `config_fields`, and the hash that keys
# the personal-widget cache (two readers with the same config share one fetch).

import hashlib
import json
import re

FIELD_TYPES = ("tickers", "choice", "int", "bool")
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")
MAX_TICKERS = 10


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(fields, raw) -> dict:
    """The normalized config (defaults filled in), or ConfigError listing every problem. Unknown keys are errors."""
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        raise ConfigError(["config must be an object"])
    by_name = {f["name"]: f for f in fields}
    errors = [f"unknown config field {k!r}" for k in raw if k not in by_name]
    out = {}
    for name, f in by_name.items():
        kind = f.get("type")
        if kind not in FIELD_TYPES:
            errors.append(f"{name}: unknown field type {kind!r}")
            continue
        value = raw.get(name, f.get("default"))
        if kind == "tickers":
            limit = min(int(f.get("max", MAX_TICKERS)), MAX_TICKERS)
            if not isinstance(value, list):
                errors.append(f"{name}: must be a list of tickers")
                continue
            norm = [v.strip().upper() for v in value if isinstance(v, str)]
            if len(norm) != len(value) or not all(TICKER_RE.match(v) for v in norm):
                errors.append(f"{name}: tickers are 1 to 12 of A-Z, 0-9, '.' and '-'")
            elif len(norm) > limit:
                errors.append(f"{name}: at most {limit} tickers")
            elif len(set(norm)) != len(norm):
                errors.append(f"{name}: repeated ticker")
            else:
                out[name] = norm
        elif kind == "choice":
            if value not in (f.get("options") or ()):
                errors.append(f"{name}: must be one of {', '.join(map(str, f.get('options') or ()))}")
            else:
                out[name] = value
        elif kind == "int":
            lo, hi = f.get("min"), f.get("max")
            if not _is_int(value) or (lo is not None and value < lo) or (hi is not None and value > hi):
                errors.append(f"{name}: must be a whole number from {lo} to {hi}")
            else:
                out[name] = value
        elif kind == "bool":
            if not isinstance(value, bool):
                errors.append(f"{name}: must be true or false")
            else:
                out[name] = value
    if errors:
        raise ConfigError(errors)
    return out


def config_hash(config: dict) -> str:
    """Stable 16-hex-char hash of a normalized config."""
    blob = json.dumps(config or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
