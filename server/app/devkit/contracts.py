"""Semantic versions and contracts (F-2).

The system decides the change type itself by comparing the old and the new version's **contract**: the output fields
with type, classification and the personal data mark, the input parameters, the list of tools or operations, and the
key. Anything outside the contract (descriptions, TTL, frequency, timeout …) is a patch.

    Beskrivelse, TTL, frekvens, timeout                       patch
    Nyt valgfrit felt, nyt tool/operation, ny valgfri parameter minor
    Felt fjernet eller omdøbt, ændret type, ny påkrævet         major
    parameter, ændret nøgle, nedklassificering

A contract is plain JSON:  {"fields": {name: {"type", "classification", "personal"}},
                            "params": {name: {"type", "required"}}, "items": [names], "key": str | None}
"""

from dataclasses import dataclass

RANK = {"white": 0, "green": 1, "yellow": 2, "red": 3}
CLASS_DA = {"white": "hvid", "green": "grøn", "yellow": "gul", "red": "rød"}
ORDER = {"patch": 0, "minor": 1, "major": 2}


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, text: str) -> "Version":
        parts = str(text).strip().lstrip("v").split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"{text} er ikke en version som 1.2.3")
        return cls(*(int(p) for p in parts))

    def bump(self, change: str) -> "Version":
        if change == "major":
            return Version(self.major + 1, 0, 0)
        if change == "minor":
            return Version(self.major, self.minor + 1, 0)
        return Version(self.major, self.minor, self.patch + 1)


FIRST = Version(1, 0, 0)


def empty() -> dict:
    return {"fields": {}, "params": {}, "items": [], "key": None}


def normalize(contract: dict | None) -> dict:
    c = {**empty(), **(contract or {})}
    c["fields"] = {k: {"type": v.get("type", "text"), "classification": v.get("classification", "yellow"),
                       "personal": bool(v.get("personal", False))} for k, v in (c["fields"] or {}).items()}
    c["params"] = {k: {"type": v.get("type", "string"), "required": bool(v.get("required", False))}
                   for k, v in (c["params"] or {}).items()}
    c["items"] = sorted(set(c["items"] or []))
    return c


def _change(kind: str, text: str, level: str) -> dict:
    return {"kind": kind, "text": text, "type": level}


def diff(old: dict | None, new: dict, *, other_changes: list[str] | None = None) -> dict:
    """{"type": patch|minor|major, "changes": [{kind (+ - ~ !), text, type}], "downclassified": [field names]}.
    `other_changes` are descriptions of non-contract edits (each a patch)."""
    a, b = normalize(old), normalize(new)
    changes: list[dict] = []
    downclassified: list[str] = []
    for name, f in b["fields"].items():
        prev = a["fields"].get(name)
        if prev is None:
            changes.append(_change("+", f"Nyt felt: {name} ({CLASS_DA[f['classification']]})", "minor"))
            continue
        if prev["type"] != f["type"]:
            changes.append(_change("!", f"Felt {name} skifter type fra {prev['type']} til {f['type']}", "major"))
        if RANK[f["classification"]] < RANK[prev["classification"]]:
            downclassified.append(name)
            changes.append(_change("!", f"Nedklassificering af {name} fra {CLASS_DA[prev['classification']]} til "
                                        f"{CLASS_DA[f['classification']]}", "major"))
        elif RANK[f["classification"]] > RANK[prev["classification"]]:
            changes.append(_change("~", f"{name} klassificeres strengere: {CLASS_DA[f['classification']]}", "minor"))
        if prev["personal"] and not f["personal"]:
            downclassified.append(name)
            changes.append(_change("!", f"{name} er ikke længere markeret som persondata", "major"))
        elif f["personal"] and not prev["personal"]:
            changes.append(_change("~", f"{name} markeres som persondata", "minor"))
    for name in a["fields"]:
        if name not in b["fields"]:
            changes.append(_change("-", f"Felt fjernet eller omdøbt: {name}", "major"))

    for name, p in b["params"].items():
        prev = a["params"].get(name)
        if prev is None:
            changes.append(_change("+", f"Ny {'påkrævet' if p['required'] else 'valgfri'} parameter: {name}",
                                   "major" if p["required"] else "minor"))
            continue
        if prev["type"] != p["type"]:
            changes.append(_change("!", f"Parameter {name} skifter type fra {prev['type']} til {p['type']}", "major"))
        if p["required"] and not prev["required"]:
            changes.append(_change("!", f"Parameter {name} bliver påkrævet", "major"))
        elif prev["required"] and not p["required"]:
            changes.append(_change("~", f"Parameter {name} bliver valgfri", "minor"))
    for name in a["params"]:
        if name not in b["params"]:
            changes.append(_change("-", f"Parameter fjernet: {name}", "major"))

    for name in b["items"]:
        if name not in a["items"]:
            changes.append(_change("+", f"Ny: {name}", "minor"))
    for name in a["items"]:
        if name not in b["items"]:
            changes.append(_change("-", f"Fjernet: {name}", "major"))

    if (a["key"] or None) != (b["key"] or None) and old is not None:
        changes.append(_change("!", f"Nøglen ændres fra {a['key'] or '—'} til {b['key'] or '—'}", "major"))

    for text in other_changes or []:
        changes.append(_change("~", text, "patch"))
    level = max((c["type"] for c in changes), key=lambda t: ORDER[t], default="patch")
    return {"type": level, "changes": changes, "downclassified": sorted(set(downclassified))}


def describe_other(old_def: dict | None, new_def: dict, keys: dict[str, str]) -> list[str]:
    """Non-contract edits between two definitions, as Danish lines: keys maps a definition key to its label."""
    old_def = old_def or {}
    return [f"{label} ændret" for key, label in keys.items() if old_def.get(key) != new_def.get(key)]
