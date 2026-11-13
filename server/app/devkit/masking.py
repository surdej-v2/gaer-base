"""Masking (F-10): the one function every row passes before it leaves the system — returned over MCP or the API,
written to the cache, written to a log or shown as a preview.

    Klassifikation   Standard                       Andre muligheder
    hvid             vises
    grøn             vises                           afrunding
    gul              afrundes eller interval         aggregering, fjernes
    rød              maskeres                        fjernes, hash, delvis visning

A field marked as personal data is masked at least as a red field. A field's strategy comes from the approved
version (an operation's masking policy, or the field's own `mask` in the catalog); it may be stricter than the
standard, never weaker (`policy_for` refuses that). Strategies, from weakest to strictest:

    show < round < interval = aggregate < partial = hash < mask < remove

`aggregate` means the field may only leave the system aggregated (a pipeline's $group); in a row it is removed.
"""

import hashlib
import hmac
import math
from datetime import date, datetime

from app import config

MASK = "•••"
STRICTNESS = {"show": 0, "round": 1, "interval": 2, "aggregate": 2, "partial": 3, "hash": 3, "mask": 4, "remove": 5}
STRATEGIES = tuple(STRICTNESS)
DEFAULT = {"white": "show", "green": "show", "yellow": "interval", "red": "mask"}
MINIMUM = {"white": 0, "green": 0, "yellow": 1, "red": 3}
LABELS = {"show": "Vis", "round": "Afrund", "interval": "Vis som interval", "aggregate": "Kun aggregeret",
          "remove": "Fjern", "mask": "Maskér", "hash": "Hash", "partial": "Delvis visning"}
RANK = {"white": 0, "green": 1, "yellow": 2, "red": 3}
CLASS_DA = {"white": "hvid", "green": "grøn", "yellow": "gul", "red": "rød"}


class MaskingError(ValueError):
    """A policy weaker than the classification allows. Danish message."""


def effective_class(field: dict) -> str:
    c = field.get("classification") or "yellow"
    if field.get("personal_data") or field.get("personal"):
        return "red" if RANK[c] < RANK["red"] else c
    return c


def default_for(field: dict) -> str:
    return DEFAULT[effective_class(field)]


def allowed_for(field: dict) -> list[str]:
    floor = MINIMUM[effective_class(field)]
    return [s for s in STRATEGIES if STRICTNESS[s] >= floor]


def policy_for(fields: list[dict], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Field name → strategy. `overrides` are the choices in a definition; a field's own `mask` in the catalog is
    used when no override names it. Raises MaskingError if a choice is weaker than the classification allows."""
    overrides = overrides or {}
    policy = {}
    for f in fields:
        choice = overrides.get(f["name"]) or f.get("mask") or default_for(f)
        if choice not in STRICTNESS:
            raise MaskingError(f"Ukendt maskering for {f['name']}: {choice}")
        if STRICTNESS[choice] < MINIMUM[effective_class(f)]:
            raise MaskingError(f"{f['name']} er {CLASS_DA[effective_class(f)]} og kan ikke vises med '{LABELS[choice]}'. "
                               f"Vælg mindst {LABELS[DEFAULT[effective_class(f)]].lower()}")
        policy[f["name"]] = choice
    return policy


def _magnitude_step(value: float) -> float:
    if value == 0:
        return 1
    return 10 ** max(0, math.floor(math.log10(abs(value))))


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def round_value(value, step: float | None = None):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    n = _number(value)
    if n is None:
        return MASK
    step = step or max(1.0, _magnitude_step(n) / 10)
    out = round(n / step) * step
    return int(out) if float(out).is_integer() else out


def interval_value(value, step: float | None = None):
    n = _number(value)
    if n is None:
        return round_value(value) if isinstance(value, (date, datetime)) else MASK
    step = step or max(1.0, _magnitude_step(n) / 2)
    low = math.floor(n / step) * step
    return f"{_fmt(low)}-{_fmt(low + step)}"


def hash_value(value) -> str:
    key = hashlib.sha256(b"gaer-mask\0" + config.settings.secret_key.encode()).digest()
    return "h:" + hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()[:16]


def partial_value(value) -> str:
    text = str(value)
    if "@" in text:
        local, _, domain = text.partition("@")
        return (local[:1] + "•••@" + domain) if local else MASK
    if text.replace(" ", "").replace("+", "").isdigit():
        digits = text.replace(" ", "")
        return "•" * max(0, len(digits) - 2) + digits[-2:]
    words = [w[:1] + "•••" for w in text.split() if w]
    return " ".join(words) if words else MASK


def mask_value(value, strategy: str, step: float | None = None):
    """One value with one strategy. `remove` and `aggregate` are handled by mask_row (the key goes)."""
    if value is None or strategy == "show":
        return value
    if strategy == "round":
        return round_value(value, step)
    if strategy == "interval":
        return interval_value(value, step)
    if strategy == "hash":
        return hash_value(value)
    if strategy == "partial":
        return partial_value(value)
    return MASK


def mask_row(row: dict, policy: dict[str, str], steps: dict[str, float] | None = None, *, strict: bool = True) -> dict:
    """A new row with the policy applied. With strict=True a column the policy does not know is masked: nothing
    leaves without a decision about it."""
    out = {}
    for k, v in row.items():
        strategy = policy.get(k, "mask" if strict else "show")
        if strategy in ("remove", "aggregate"):
            continue
        out[k] = mask_value(v, strategy, (steps or {}).get(k))
    return out


def mask_rows(rows: list[dict], policy: dict[str, str], steps: dict[str, float] | None = None, *,
              strict: bool = True) -> list[dict]:
    return [mask_row(r, policy, steps, strict=strict) for r in rows]


def masked_fields(policy: dict[str, str]) -> list[str]:
    return sorted(k for k, v in policy.items() if v != "show")


def mask_args(args: dict | None, sensitive: set[str]) -> dict | None:
    """Arguments for a log line (MCP-14): values of sensitive parameters are masked."""
    if args is None:
        return None
    return {k: (MASK if k in sensitive and v not in (None, "") else v) for k, v in args.items()}


def strictest(classes: list[str]) -> str:
    """F-9: a computed field inherits the strictest classification of its sources (hvid < grøn < gul < rød)."""
    return max(classes or ["white"], key=lambda c: RANK[c])
