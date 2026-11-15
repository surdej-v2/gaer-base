"""Expressions in flows (Action-studie): `{{ … }}` inside the values of a step, evaluated against the run's data.

No code is ever executed. The language is small and closed: literals, paths into JSON data, operators and a fixed set
of functions — the same idea as the expressions in Logic Apps and Power Automate, with a readable syntax:

    "Hej {{ trigger.body.name }}"                         a string with a value filled in
    "{{ steps.hent.body.items }}"                         one expression alone keeps its type (a list stays a list)
    "{{ length(steps.hent.body.items) > 0 and vars.ok }}" operators: == != < <= > >= + - * / % and or not in ??
    "{{ filter(steps.hent.body.items, 'item.active') }}"  list functions take a sub-expression as text, with `item` and `index`

What a run exposes: `trigger` (type, body, headers, event, data, at), `steps.<id>` (each step's output), `vars`
(set by the step `set`), `run` (id, flow, started_at), and inside a loop `item` and `index`. A missing key or a path
through null gives null instead of an error, so `trigger.body.customer.name` is safe to write.
"""

import base64
import csv
import hashlib
import io
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

MAX_LENGTH = 4000
MAX_DEPTH = 64
_TOKEN = re.compile(r"""
    (?P<ws>\s+) |
    (?P<num>\d+(?:\.\d+)?) |
    (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*") |
    (?P<name>[A-Za-z_][A-Za-z0-9_]*) |
    (?P<op>\?\?|==|!=|<=|>=|[-+*/%<>()\[\]{},.:?])
""", re.X)
KEYWORDS = {"and", "or", "not", "in", "true", "false", "null"}


class ExprError(ValueError):
    """A bad expression: syntax, an unknown function, or a value that cannot be used that way. The message is Danish."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ── Tokens and parsing ──────────────────────────────────────────────────────

def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise ExprError(f"Uventet tegn {text[pos]!r} i udtrykket")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        value = m.group(kind)
        if kind == "name" and value in KEYWORDS:
            kind = "kw"
        out.append((kind, value))
    out.append(("end", ""))
    return out


class _Parser:
    """Recursive descent → a small tree of tuples. Precedence (low → high): ?: · ?? · or · and · not · comparisons · + -
    · * / % · unary - · postfix (.name, [index], call)."""

    def __init__(self, text: str):
        if len(text) > MAX_LENGTH:
            raise ExprError("Udtrykket er for langt")
        self.toks = _tokens(text)
        self.i = 0
        self.depth = 0

    def peek(self, kind=None, value=None) -> bool:
        k, v = self.toks[self.i]
        return (kind is None or k == kind) and (value is None or v == value)

    def take(self, kind=None, value=None) -> str:
        k, v = self.toks[self.i]
        if (kind is not None and k != kind) or (value is not None and v != value):
            want = value or kind
            raise ExprError(f"Forventede {want!r}, men fandt {v or 'slutningen'!r}")
        self.i += 1
        return v

    def parse(self):
        node = self.ternary()
        if not self.peek("end"):
            raise ExprError(f"Uventet {self.toks[self.i][1]!r} efter udtrykket")
        return node

    def nest(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError("Udtrykket er for dybt")

    def ternary(self):
        cond = self.coalesce()
        if self.peek("op", "?"):
            self.take()
            a = self.ternary()
            self.take("op", ":")
            b = self.ternary()
            return ("cond", cond, a, b)
        return cond

    def coalesce(self):
        node = self.or_()
        while self.peek("op", "??"):
            self.take()
            node = ("coalesce", node, self.or_())
        return node

    def or_(self):
        node = self.and_()
        while self.peek("kw", "or"):
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek("kw", "and"):
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.peek("kw", "not"):
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        node = self.add()
        while True:
            if self.peek("op") and self.toks[self.i][1] in ("==", "!=", "<", "<=", ">", ">="):
                op = self.take()
                node = ("bin", op, node, self.add())
            elif self.peek("kw", "in"):
                self.take()
                node = ("bin", "in", node, self.add())
            else:
                return node

    def add(self):
        node = self.mul()
        while self.peek("op") and self.toks[self.i][1] in ("+", "-"):
            op = self.take()
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.peek("op") and self.toks[self.i][1] in ("*", "/", "%"):
            op = self.take()
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek("op", "-"):
            self.take()
            return ("neg", self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while True:
            if self.peek("op", "."):
                self.take()
                node = ("get", node, ("lit", self.take("name")))
            elif self.peek("op", "["):
                self.take()
                idx = self.ternary()
                self.take("op", "]")
                node = ("get", node, idx)
            elif self.peek("op", "(") and node[0] == "name":
                self.take()
                args = []
                while not self.peek("op", ")"):
                    args.append(self.ternary())
                    if self.peek("op", ","):
                        self.take()
                self.take("op", ")")
                node = ("call", node[1], args)
            else:
                return node

    def primary(self):
        self.nest()
        try:
            k, v = self.toks[self.i]
            if k == "num":
                self.take()
                return ("lit", float(v) if "." in v else int(v))
            if k == "str":
                self.take()
                return ("lit", _unquote(v))
            if k == "kw" and v in ("true", "false", "null"):
                self.take()
                return ("lit", {"true": True, "false": False, "null": None}[v])
            if k == "name":
                self.take()
                return ("name", v)
            if k == "op" and v == "(":
                self.take()
                node = self.ternary()
                self.take("op", ")")
                return node
            if k == "op" and v == "[":
                self.take()
                items = []
                while not self.peek("op", "]"):
                    items.append(self.ternary())
                    if self.peek("op", ","):
                        self.take()
                self.take("op", "]")
                return ("list", items)
            if k == "op" and v == "{":
                self.take()
                pairs = []
                while not self.peek("op", "}"):
                    key = self.take("str") if self.peek("str") else self.take("name")
                    key = _unquote(key) if key[:1] in "'\"" else key
                    self.take("op", ":")
                    pairs.append((key, self.ternary()))
                    if self.peek("op", ","):
                        self.take()
                self.take("op", "}")
                return ("dict", pairs)
            raise ExprError(f"Uventet {v or 'slutning'!r} i udtrykket")
        finally:
            self.depth -= 1


def _unquote(text: str) -> str:
    body = text[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def parse(text: str):
    return _Parser(text).parse()


def segments(text: str):
    """The parts of a template in order: (False, literal) and (True, expression). A linear scan — `{{` without a
    closing `}}` stays literal text."""
    pos = 0
    while True:
        start = text.find("{{", pos)
        if start < 0:
            break
        end = text.find("}}", start + 2)
        if end < 0:
            break
        if start > pos:
            yield False, text[pos:start]
        yield True, text[start + 2:end]
        pos = end + 2
    if pos < len(text):
        yield False, text[pos:]


def check(text: str) -> None:
    """Syntax only (for saving a definition): raises ExprError."""
    for is_expr, part in segments(text):
        if is_expr:
            parse(part)


# ── Evaluation ──────────────────────────────────────────────────────────────

def truthy(v) -> bool:
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, (str, list, dict)):
        return len(v) > 0
    return bool(v)


def _get(obj, key):
    if obj is None:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        return obj.get(_str(key)) if not isinstance(key, bool) else None
    if isinstance(obj, (list, str)) and isinstance(key, (int, float)) and not isinstance(key, bool):
        i = int(key)
        return obj[i] if -len(obj) <= i < len(obj) else None
    if isinstance(obj, (list, str)) and key == "length":
        return len(obj)
    return None


def _num(v, what: str):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ExprError(f"{what} kræver tal, ikke {_type_name(v)}")
    return v


def _type_name(v) -> str:
    return {type(None): "null", bool: "sand/falsk", int: "tal", float: "tal", str: "tekst", list: "liste", dict: "objekt"}.get(type(v), type(v).__name__)


def _binary(op: str, a, b):
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "in":
        if isinstance(b, str):
            return str(a) in b
        if isinstance(b, dict):
            return str(a) in b
        return a in (b or [])
    if op == "+":
        if isinstance(a, str) or isinstance(b, str):
            return _str(a) + _str(b)
        if isinstance(a, list) and isinstance(b, list):
            return a + b
        return _num(a, "+") + _num(b, "+")
    if op in ("<", "<=", ">", ">="):
        if isinstance(a, str) and isinstance(b, str):
            pass
        else:
            a, b = _num(a, op), _num(b, op)
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    a, b = _num(a, op), _num(b, op)
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        raise ExprError("Division med nul")
    return a / b if op == "/" else a % b


def evaluate_tree(node, ctx: dict):
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "name":
        return ctx.get(node[1])
    if kind == "get":
        return _get(evaluate_tree(node[1], ctx), evaluate_tree(node[2], ctx))
    if kind == "list":
        return [evaluate_tree(n, ctx) for n in node[1]]
    if kind == "dict":
        return {k: evaluate_tree(n, ctx) for k, n in node[1]}
    if kind == "neg":
        return -_num(evaluate_tree(node[1], ctx), "-")
    if kind == "not":
        return not truthy(evaluate_tree(node[1], ctx))
    if kind == "and":
        left = evaluate_tree(node[1], ctx)
        return evaluate_tree(node[2], ctx) if truthy(left) else left
    if kind == "or":
        left = evaluate_tree(node[1], ctx)
        return left if truthy(left) else evaluate_tree(node[2], ctx)
    if kind == "coalesce":
        left = evaluate_tree(node[1], ctx)
        return left if left is not None else evaluate_tree(node[2], ctx)
    if kind == "cond":
        return evaluate_tree(node[2], ctx) if truthy(evaluate_tree(node[1], ctx)) else evaluate_tree(node[3], ctx)
    if kind == "bin":
        return _binary(node[1], evaluate_tree(node[2], ctx), evaluate_tree(node[3], ctx))
    if kind == "call":
        fn = FUNCTIONS.get(node[1])
        if fn is None:
            raise ExprError(f"Ukendt funktion {node[1]}()")
        if node[1] == "if":   # lazy: only the chosen branch is evaluated
            if len(node[2]) != 3:
                raise ExprError("if() tager tre argumenter: betingelse, hvis sand, hvis falsk")
            return evaluate_tree(node[2][1] if truthy(evaluate_tree(node[2][0], ctx)) else node[2][2], ctx)
        args = [evaluate_tree(n, ctx) for n in node[2]]
        try:
            return fn(ctx, *args)
        except ExprError:
            raise
        except (TypeError, ValueError, AttributeError, KeyError, IndexError, OverflowError) as e:
            raise ExprError(f"{node[1]}(): {str(e)[:120] or 'forkerte argumenter'}")
    raise ExprError("Ugyldigt udtryk")


def evaluate(text: str, ctx: dict):
    return evaluate_tree(parse(text), ctx)


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _single(value: str) -> str | None:
    """The expression when the whole string is exactly one `{{ … }}` and nothing else (plain string checks, no regex)."""
    text = value.strip()
    if not (text.startswith("{{") and text.endswith("}}")) or len(text) < 4:
        return None
    inner = text[2:-2]
    return None if "}}" in inner or "{{" in inner else inner


def render(value, ctx: dict):
    """Fills `{{ … }}` into a value: strings are templated (one expression alone keeps its type), dicts and lists
    recursively. Everything else is returned as it is."""
    if isinstance(value, str):
        if "{{" not in value:
            return value
        single = _single(value)
        if single is not None:
            return evaluate(single, ctx)
        return "".join(_str(evaluate(part, ctx)) if is_expr else part for is_expr, part in segments(value))
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def to_text(v) -> str:
    """A value as text: null → "", booleans → true/false, objects and lists → JSON."""
    return _str(v)


def to_datetime(v) -> datetime:
    return _dt(v)


def type_name(v) -> str:
    return _type_name(v)


# ── Functions ───────────────────────────────────────────────────────────────

def _each(ctx: dict, items, expr: str):
    if items is None:
        return []
    if not isinstance(items, list):
        raise ExprError("Første argument skal være en liste")
    if not isinstance(expr, str):
        raise ExprError("Udtrykket skal være tekst, fx 'item.active'")
    tree = parse(expr)
    for i, item in enumerate(items):
        yield item, evaluate_tree(tree, {**ctx, "item": item, "index": i})


def _dt(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    if not isinstance(v, str) or not v.strip():
        raise ExprError("Datoen mangler")
    text = v.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        try:
            d = datetime.strptime(text, "%d-%m-%Y")
        except ValueError:
            raise ExprError(f"{v!r} er ikke en dato (brug ISO 8601, fx 2026-01-31T08:00:00+01:00)")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat()


def _sortable(v):
    return (0, v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (1, _str(v))


def _csv(ctx, rows, sep=",") -> str:
    if not isinstance(rows, list):
        raise ExprError("csv() tager en liste af objekter")
    out = io.StringIO()
    if rows and all(isinstance(r, dict) for r in rows):
        keys: list[str] = []
        for r in rows:
            keys += [k for k in r if k not in keys]
        w = csv.DictWriter(out, fieldnames=keys, delimiter=str(sep)[:1] or ",", extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _str(v) for k, v in r.items()})
    else:
        w = csv.writer(out, delimiter=str(sep)[:1] or ",", lineterminator="\n")
        for r in rows:
            w.writerow([_str(x) for x in r] if isinstance(r, list) else [_str(r)])
    return out.getvalue()


def _to_int(ctx, v, default=None):
    try:
        return int(float(v)) if not isinstance(v, bool) else int(v)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise ExprError(f"{_str(v)[:40]!r} er ikke et heltal")


def _to_float(ctx, v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise ExprError(f"{_str(v)[:40]!r} er ikke et tal")


def _json_parse(ctx, text):
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        raise ExprError("Teksten er ikke gyldig JSON")


def _round(ctx, v, digits=0):
    return round(_num(v, "round"), int(digits)) if int(digits) else int(round(_num(v, "round")))


def _substring(ctx, s, start, length=None):
    s = _str(s)
    start = int(start)
    return s[start:] if length is None else s[start:start + int(length)]


def _format_date(ctx, v, fmt="%d-%m-%Y %H:%M", tz="Europe/Copenhagen"):
    from zoneinfo import ZoneInfo
    d = _dt(v).astimezone(ZoneInfo(str(tz)))
    if "%" not in fmt:   # dd-MM-yyyy HH:mm style
        table = [("yyyy", "%Y"), ("MM", "%m"), ("dd", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S")]
        for a, b in table:
            fmt = fmt.replace(a, b)
    return d.strftime(fmt)


FUNCTIONS = {
    # text
    "length": lambda ctx, v: len(v) if isinstance(v, (str, list, dict)) else 0,
    "count": lambda ctx, v: len(v) if isinstance(v, (str, list, dict)) else 0,
    "lower": lambda ctx, s: _str(s).lower(),
    "upper": lambda ctx, s: _str(s).upper(),
    "trim": lambda ctx, s: _str(s).strip(),
    "replace": lambda ctx, s, a, b: _str(s).replace(_str(a), _str(b)),
    "split": lambda ctx, s, sep=",": _str(s).split(_str(sep)),
    "join": lambda ctx, items, sep=", ": _str(sep).join(_str(x) for x in (items or [])),
    "contains": lambda ctx, hay, needle: (_str(needle) in hay) if isinstance(hay, (str, dict)) else (needle in (hay or [])),
    "startsWith": lambda ctx, s, p: _str(s).startswith(_str(p)),
    "endsWith": lambda ctx, s, p: _str(s).endswith(_str(p)),
    "indexOf": lambda ctx, s, p: _str(s).find(_str(p)) if isinstance(s, str) else (s.index(p) if p in (s or []) else -1),
    "substring": _substring,
    "concat": lambda ctx, *parts: "".join(_str(p) for p in parts),
    "string": lambda ctx, v: _str(v),
    "int": _to_int,
    "float": _to_float,
    "bool": lambda ctx, v: truthy(v),
    "json": _json_parse,
    "toJson": lambda ctx, v, indent=None: json.dumps(v, ensure_ascii=False, default=str, indent=int(indent) if indent else None),
    "coalesce": lambda ctx, *vals: next((v for v in vals if v is not None), None),
    "default": lambda ctx, v, d: d if v is None or v == "" else v,
    "if": lambda ctx, c, a, b: a if truthy(c) else b,   # evaluated lazily in evaluate_tree
    "empty": lambda ctx, v: not truthy(v),
    "base64": lambda ctx, s: base64.b64encode(_str(s).encode()).decode(),
    "base64Decode": lambda ctx, s: base64.b64decode(_str(s)).decode("utf-8", "replace"),
    "urlEncode": lambda ctx, s: quote(_str(s), safe=""),
    "sha256": lambda ctx, s: hashlib.sha256(_str(s).encode()).hexdigest(),
    "uuid": lambda ctx: str(uuid.uuid4()),
    "csv": _csv,
    # numbers
    "round": _round,
    "abs": lambda ctx, v: abs(_num(v, "abs")),
    "floor": lambda ctx, v: math.floor(_num(v, "floor")),
    "ceil": lambda ctx, v: math.ceil(_num(v, "ceil")),
    "min": lambda ctx, *vals: min(_num(v, "min") for v in (vals[0] if len(vals) == 1 and isinstance(vals[0], list) else vals)),
    "max": lambda ctx, *vals: max(_num(v, "max") for v in (vals[0] if len(vals) == 1 and isinstance(vals[0], list) else vals)),
    "sum": lambda ctx, items: sum(_num(v, "sum") for v in (items or [])),
    "avg": lambda ctx, items: (sum(_num(v, "avg") for v in items) / len(items)) if items else None,
    # lists and objects
    "first": lambda ctx, items: (items[0] if items else None) if isinstance(items, (list, str)) else None,
    "last": lambda ctx, items: (items[-1] if items else None) if isinstance(items, (list, str)) else None,
    "keys": lambda ctx, obj: list(obj.keys()) if isinstance(obj, dict) else [],
    "values": lambda ctx, obj: list(obj.values()) if isinstance(obj, dict) else [],
    "get": lambda ctx, obj, path: _path(obj, path),
    "merge": lambda ctx, *objs: {k: v for o in objs if isinstance(o, dict) for k, v in o.items()},
    "range": lambda ctx, n, m=None: list(range(int(n))) if m is None else list(range(int(n), int(m))),
    "sort": lambda ctx, items: sorted(items or [], key=_sortable),
    "sortBy": lambda ctx, items, expr: [it for it, _ in sorted(_each(ctx, items, expr), key=lambda p: _sortable(p[1]))],
    "reverse": lambda ctx, items: list(reversed(items or [])),
    "unique": lambda ctx, items: list({json.dumps(x, sort_keys=True, default=str): x for x in (items or [])}.values()),
    "flatten": lambda ctx, items: [y for x in (items or []) for y in (x if isinstance(x, list) else [x])],
    "slice": lambda ctx, items, a, b=None: (items or [])[int(a):(int(b) if b is not None else None)],
    "filter": lambda ctx, items, expr: [it for it, v in _each(ctx, items, expr) if truthy(v)],
    "map": lambda ctx, items, expr: [v for _, v in _each(ctx, items, expr)],
    "find": lambda ctx, items, expr: next((it for it, v in _each(ctx, items, expr) if truthy(v)), None),
    "any": lambda ctx, items, expr: any(truthy(v) for _, v in _each(ctx, items, expr)),
    "all": lambda ctx, items, expr: all(truthy(v) for _, v in _each(ctx, items, expr)),
    "groupBy": lambda ctx, items, expr: _group(ctx, items, expr),
    # dates (ISO 8601 text)
    "now": lambda ctx: _iso(datetime.now(timezone.utc)),
    "today": lambda ctx: datetime.now(timezone.utc).date().isoformat(),
    "date": lambda ctx, v: _dt(v).date().isoformat(),
    "addDays": lambda ctx, v, n: _iso(_dt(v) + timedelta(days=float(n))),
    "addHours": lambda ctx, v, n: _iso(_dt(v) + timedelta(hours=float(n))),
    "addMinutes": lambda ctx, v, n: _iso(_dt(v) + timedelta(minutes=float(n))),
    "formatDate": _format_date,
    "parseDate": lambda ctx, v, fmt=None: _iso(datetime.strptime(_str(v), fmt).replace(tzinfo=timezone.utc) if fmt else _dt(v)),
    "diffMinutes": lambda ctx, a, b: (_dt(b) - _dt(a)).total_seconds() / 60,
    "timestamp": lambda ctx, v=None: int((_dt(v) if v is not None else datetime.now(timezone.utc)).timestamp()),
}


def _path(obj, path):
    for part in str(path or "").split("."):
        if part == "":
            continue
        obj = _get(obj, int(part) if part.isdigit() and isinstance(obj, list) else part)
    return obj


def _group(ctx, items, expr):
    out: dict = {}
    for it, v in _each(ctx, items, expr):
        out.setdefault(_str(v), []).append(it)
    return out


# Shown in the editor's help: name, signature, what it does (Danish).
REFERENCE = [
    ("length(x)", "Antal tegn eller elementer"), ("lower(s) · upper(s) · trim(s)", "Tekst i små/store bogstaver, uden mellemrum rundt om"),
    ("replace(s, a, b)", "Erstat a med b"), ("split(s, sep) · join(liste, sep)", "Del tekst op · sæt liste sammen"),
    ("contains(x, y) · startsWith · endsWith · indexOf", "Søg i tekst eller liste"), ("substring(s, start, længde)", "Udsnit af tekst"),
    ("concat(a, b, …) · string(x) · int(x) · float(x) · bool(x)", "Sæt sammen · konvertér"),
    ("json(tekst) · toJson(x)", "Læs JSON · skriv JSON"), ("coalesce(a, b, …) · default(x, d) · a ?? b", "Første værdi, der ikke er null"),
    ("if(betingelse, a, b) · betingelse ? a : b", "Vælg mellem to værdier"), ("empty(x)", "Tom eller null"),
    ("first(liste) · last(liste) · slice(liste, fra, til) · reverse · flatten · unique · sort", "Lister"),
    ("filter(liste, 'item.aktiv') · map(liste, 'item.id') · find · any · all · sortBy · groupBy", "Lister med et udtryk pr. element (item, index)"),
    ("keys(obj) · values(obj) · get(obj, 'a.b.0') · merge(a, b)", "Objekter"),
    ("sum(liste) · avg · min · max · round(x, n) · abs · floor · ceil", "Tal"),
    ("now() · today() · date(d) · addDays(d, n) · addHours · addMinutes · diffMinutes(a, b)", "Datoer som ISO 8601-tekst"),
    ("formatDate(d, 'dd-MM-yyyy HH:mm', 'Europe/Copenhagen') · parseDate(s, '%d/%m/%Y') · timestamp(d)", "Formatér og læs datoer"),
    ("csv(rækker, ';') · base64(s) · base64Decode(s) · urlEncode(s) · sha256(s) · uuid()", "Filer og koder"),
]
