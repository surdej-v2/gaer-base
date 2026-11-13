"""Data export and import (/admin/data/transfer).

A package is a zip:

    manifest.json            format, version, source, migration, areas, personal mode, tables (rows, sha256)
    data/<table>.json        one JSON array per table: each row is the table's primary key plus its exported columns,
                             values exactly as Postgres' to_jsonb gives them (timestamps in UTC)
    data/<table>.csv         the same as CSV (format "csv": for spreadsheets, cannot be imported)
    blobs/<file id>          the content of a file, when the export included the files' content

With a password the whole zip is encrypted (MAGIC + scrypt salt + AES-256-GCM in 1 MiB chunks, the last one marked),
so neither the manifest nor the data can be read or changed without it.

Rows refer to each other by the source's primary keys. On import every table is matched in dependency order
(TABLES) and the source keys are mapped to this instance's keys, by a natural key (an e-mail, a slug, a path …) or by
the id itself. What happens to a row that exists already, to references to users that do not exist here and to
errors is chosen per import; a dry run is the same import rolled back at the end. Every row an import writes is
audited (create/update, detail.transfer_id) and recorded in data_transfer_changes for UNDO_DAYS, so it can be undone.

Never in a package: secrets (values in Hemmeligheder, webhook signing secrets and auth headers), passkeys, security
keys, authenticator apps, sessions, one-time codes, the licence key and environment variables.
"""

import csv
import hashlib
import io
import json
import logging
import os
import secrets
import struct
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.types.json import Jsonb

from app import config, db, events, jobs, secrets_box, settings_store, webhooks
from app.logs import RequestMeta, audit
from app.storage import get_storage

log = logging.getLogger("gaer.transfer")

FORMAT = "gaer-transfer"
VERSION = 1
DOWNLOAD_HOURS = 24          # an export can be downloaded this long, then the package is deleted
IMPORT_PACKAGE_DAYS = 7      # an uploaded package is kept this long for dry runs and the import
UNDO_DAYS = 7                # an import can be undone this long
MAX_DIFFERENCES = 50
MAX_ERRORS = 200
PASSWORD_PURPOSE = "transfer.password"

PERSONAL_MODES = ("with", "pseudo", "without")
FORMATS = ("json", "csv")
CONFLICTS = ("skip", "overwrite", "merge", "copy")
MATCHES = ("natural", "id")
MISSING = ("create", "skip", "me")
ON_ERROR = ("all", "continue")

INSERT_ONLY_TIMES = {"created_at", "added_at"}
AUTHOR_COLUMNS = {"created_by", "uploaded_by", "added_by", "set_by"}
EDITOR_COLUMNS = {"updated_by"}

# Settings that describe how the instance behaves. Left out: storage keys of uploaded images (the objects are not in
# the package) and values the app writes itself (last runs, syncs, rotations).
SETTINGS_KEYS = tuple(k for k in settings_store.DEFAULTS if k not in {
    "logo_key", "og_images", "retention_last_run", "mail_relay_rotated_at", "mail_relay_paused", "mail_last_sync",
    "mail_last_backup"})


class PackageError(Exception):
    """The package cannot be read (wrong password, damaged, another format). The message is shown to the admin."""


class RowError(Exception):
    """A row that cannot be imported. Counted as an error."""


class Skip(Exception):
    """A row that is left out on purpose (a reference to something that is not here). Counted as skipped."""


# ── What can be exported ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Ref:
    table: str
    kind: str = "owner"   # owner: the row needs it · soft: null when it is not here · skip: leave the row out


@dataclass
class Table:
    name: str
    title: str
    area: str
    columns: tuple[str, ...]                 # exported besides the primary key
    key: tuple[str, ...]                     # natural key, compared after references are mapped
    pk: tuple[str, ...] = ("id",)
    serial: bool = True                      # the primary key is made by the database
    refs: dict[str, Ref] = field(default_factory=dict)
    where: str = ""                          # which rows are exported (SQL on alias t)
    order: str = "t.id"
    copy: dict[str, str] = field(default_factory=dict)   # "Opret som kopi": column → "slug" | "title"
    personal: bool = False                   # rows are about a person: reports show only the source id
    personal_columns: tuple[str, ...] = ()   # emptied in the modes pseudo and without
    insert_only: tuple[str, ...] = ()        # set when the row is created, never changed on an existing row
    updatable: bool = True                   # False: an existing row is never changed
    lookup_only: bool = False                # exported to resolve references, never written
    importable: bool = True
    time_column: str | None = None           # the period filter for logs and clips


NOT_TEST_USER = "(select id from users where not impersonation_only)"

TABLES: list[Table] = [
    Table("users", "Brugere", "users",
          ("email", "phone", "display_name", "locale", "time_zone", "audience", "role", "admin_roles", "blocked_at",
           "created_at"),
          key=("email", "phone"), where="not t.impersonation_only", personal=True,
          insert_only=("role", "admin_roles")),
    Table("feature_rings", "Brugernes ring", "users", ("ring", "set_by", "updated_at"),
          key=("user_id",), pk=("user_id",), serial=False, order="t.user_id",
          refs={"user_id": Ref("users"), "set_by": Ref("users", "soft")},
          where=f"t.user_id in {NOT_TEST_USER}", personal=True),
    Table("groups", "Grupper", "groups", ("slug", "name", "description", "notify_email", "created_by", "created_at",
                                          "updated_at"),
          key=("slug",), refs={"created_by": Ref("users", "soft")}, copy={"slug": "slug", "name": "title"},
          personal_columns=("notify_email",)),
    Table("group_members", "Gruppemedlemmer", "groups", ("role", "added_by", "added_at"),
          key=("group_id", "user_id"), pk=("group_id", "user_id"), serial=False, order="t.group_id, t.user_id",
          refs={"group_id": Ref("groups"), "user_id": Ref("users"), "added_by": Ref("users", "soft")},
          where=f"t.user_id in {NOT_TEST_USER}", personal=True),
    Table("web_pages", "Website-sider", "web", ("slug", "lang", "title", "description", "body", "nav_order",
                                                "published", "updated_at"),
          key=("slug", "lang"), copy={"slug": "slug", "title": "title"}),
    Table("clips", "Klip", "clips", ("user_id", "source", "title", "body", "url", "created_at", "updated_at"),
          key=("user_id", "created_at", "title"), refs={"user_id": Ref("users")}, copy={"title": "title"},
          where=f"t.user_id in {NOT_TEST_USER}", personal=True, time_column="created_at"),
    Table("file_folders", "Mapper", "files", ("space", "user_id", "path", "created_by", "created_at"),
          key=("space", "user_id", "path"), refs={"user_id": Ref("users"), "created_by": Ref("users", "soft")},
          where=f"t.space in ('user', 'shared') and (t.user_id is null or t.user_id in {NOT_TEST_USER})",
          updatable=False),
    Table("files", "Filer", "files", ("space", "user_id", "folder", "filename", "content_type", "size_bytes",
                                      "uploaded_by", "created_at", "updated_at"),
          key=("space", "user_id", "folder", "filename"),
          refs={"user_id": Ref("users"), "uploaded_by": Ref("users", "soft")},
          where=f"t.kind = 'file' and t.space in ('user', 'shared') and (t.user_id is null or t.user_id in {NOT_TEST_USER})",
          updatable=False),
    Table("meta_dimensions", "Dimensioner", "meta", ("slug", "label", "description", "color", "locked", "created_by",
                                                     "created_at", "updated_at"),
          key=("slug",), refs={"created_by": Ref("users", "soft")}, copy={"slug": "slug", "label": "title"}),
    Table("meta_nodes", "Noder", "meta", ("dimension_id", "parent_id", "path", "slug", "label", "description", "depth",
                                          "sort_order", "archived_at", "created_by", "updated_by", "created_at",
                                          "updated_at"),
          key=("path",), where="t.owner_id is null", order="t.depth, t.id",
          refs={"dimension_id": Ref("meta_dimensions"), "parent_id": Ref("meta_nodes"),
                "created_by": Ref("users", "soft"), "updated_by": Ref("users", "soft")},
          insert_only=("dimension_id", "parent_id", "path", "depth")),
    Table("meta_objects", "Taggede objekter", "meta", ("kind", "ref", "title", "description", "created_by",
                                                       "updated_by", "created_at", "updated_at"),
          key=("kind", "ref"), refs={"created_by": Ref("users", "soft"), "updated_by": Ref("users", "soft")},
          where="""exists (select 1 from meta_assignments a join meta_nodes n on n.id = a.node_id
                           where a.object_id = t.id and n.owner_id is null)"""),
    Table("meta_assignments", "Tags", "meta", ("created_by", "created_at"),
          key=("object_id", "node_id"), pk=("object_id", "node_id"), serial=False, order="t.object_id, t.node_id",
          refs={"object_id": Ref("meta_objects"), "node_id": Ref("meta_nodes"), "created_by": Ref("users", "soft")},
          where="exists (select 1 from meta_nodes n where n.id = t.node_id and n.owner_id is null)", updatable=False),
    Table("settings", "Indstillinger", "settings", ("value", "updated_at"), key=("key",), pk=("key",), serial=False,
          order="t.key", where="t.key in (" + ", ".join(f"'{k}'" for k in SETTINGS_KEYS) + ")"),
    Table("features", "Features", "features", ("title", "description", "area", "ring", "observe", "created_by",
                                               "created_at", "updated_at"),
          key=("key",), pk=("key",), serial=False, order="t.key", refs={"created_by": Ref("users", "soft")}),
    Table("webhooks", "Udgående webhooks", "webhooks", ("name", "url", "events", "active", "timeout_seconds",
                                                        "max_attempts", "created_by", "created_at", "updated_at"),
          key=("name",), refs={"created_by": Ref("users", "soft")}, copy={"name": "title"}, insert_only=("active",)),
    Table("webhook_endpoints", "Indgående webhooks", "webhooks",
          ("name", "key", "verify", "header_name", "handler", "event_header", "event_field", "id_header", "allowed_ips",
           "max_body_kb", "max_per_minute", "tolerance_seconds", "active", "created_by", "created_at", "updated_at"),
          key=("key",), refs={"created_by": Ref("users", "soft")}, copy={"key": "slug", "name": "title"},
          insert_only=("active",)),
    Table("data_sources", "API-kilder", "data", ("key", "name", "kind", "spec_yaml", "hosts", "created_at",
                                                 "updated_at"),
          key=("key",)),
    Table("datasets", "Datasæt", "data", ("source_id", "key", "name", "description", "owner", "classification",
                                          "personal_data", "mcp_exposed", "version", "created_at", "updated_at"),
          key=("source_id", "key"), refs={"source_id": Ref("data_sources")}, where="t.source_id is not null",
          insert_only=("source_id",)),
    Table("dataset_fields", "Felter i datasæt", "data", ("dataset_id", "name", "path", "label", "description",
                                                         "data_type", "classification", "position"),
          key=("dataset_id", "name"), refs={"dataset_id": Ref("datasets")},
          where="t.dataset_id in (select id from datasets where source_id is not null)", insert_only=("dataset_id",)),
    Table("mcp_endpoints", "MCP-endpoints", "mcp", ("name", "description", "instructions", "audience", "enabled",
                                                    "created_by", "created_at", "updated_at"),
          key=("key",), pk=("key",), serial=False, order="t.key", where="not t.builtin",
          refs={"created_by": Ref("users", "soft")}),
    Table("mcp_tools", "MCP-værktøjer", "mcp", ("name",), key=("name",), lookup_only=True),
    Table("mcp_tool_endpoints", "Værktøjer på endpoints", "mcp", (), key=("tool_id", "endpoint"),
          pk=("tool_id", "endpoint"), serial=False, order="t.tool_id, t.endpoint",
          refs={"tool_id": Ref("mcp_tools", "skip"), "endpoint": Ref("mcp_endpoints", "skip")}, updatable=False),
    Table("adoption_journeys", "Adoption-forløb", "adoption", ("name", "status", "audience", "goal_pct", "start_at",
                                                               "end_at", "created_by", "created_at", "updated_at"),
          key=("name",), refs={"created_by": Ref("users", "soft")}, copy={"name": "title"}),
    Table("adoption_steps", "Trin i forløb", "adoption", ("journey_id", "position", "label", "match_type", "pattern"),
          key=("journey_id", "position"), refs={"journey_id": Ref("adoption_journeys")}, insert_only=("journey_id",)),
    Table("audit_log", "Audit-log", "audit_log", ("ts", "actor_user_id", "action", "table_name", "row_id", "before",
                                                  "after", "ip", "detail"),
          key=("id",), refs={"actor_user_id": Ref("users", "soft")}, personal=True, importable=False,
          time_column="ts"),
    Table("auth_log", "Auth-log", "auth_log", ("ts", "event", "result", "user_id", "phone_masked", "phone_hash", "ip",
                                               "device", "detail"),
          key=("id",), refs={"user_id": Ref("users", "soft")}, personal=True, importable=False, time_column="ts"),
]
TABLE = {t.name: t for t in TABLES}


@dataclass(frozen=True)
class Area:
    key: str
    title: str
    description: str
    group: str
    importable: bool = True


AREAS: list[Area] = [
    Area("users", "Brugere og profiler",
         "E-mail, telefon, navn, sprog, målgruppe og ring. Uden passkeys, sessioner og koder.", "Brugere og adgang"),
    Area("roles", "Admin-roller", "Hvem er admin, og med hvilke roller. Kræver Brugere og profiler.",
         "Brugere og adgang"),
    Area("groups", "Grupper og medlemmer", "Grupper med medlemmer og managere.", "Brugere og adgang"),
    Area("web", "Website (/w)", "Markdown-siderne på dansk og engelsk.", "Indhold"),
    Area("clips", "Klip", "Alle brugeres klip.", "Indhold"),
    Area("files", "Filer", "Mapper og filer i hjemmemapper og /shared. Indholdet vælger du under Optioner.", "Indhold"),
    Area("meta", "Træstrukturer og tags", "Delte dimensioner, noder og tagging af objekter.", "Indhold"),
    Area("settings", "Indstillinger", "Tilmelding, forside, samtykke, beskeder og kill-regler.", "Konfiguration"),
    Area("features", "Features", "Features og hvilken ring de er åbne for.", "Konfiguration"),
    Area("webhooks", "Webhooks", "Udgående og indgående endpoints, uden deres hemmeligheder.", "Konfiguration"),
    Area("data", "API-kilder og datakatalog", "Kildebeskrivelser og datasæt. Henvisninger til hemmeligheder beholdes.",
         "Konfiguration"),
    Area("mcp", "MCP-endpoints", "Endpoints fra MCP Studio og hvilke værktøjer de tilbyder.", "Konfiguration"),
    Area("adoption", "Adoption-forløb", "Forløb og trin, ikke besøgene.", "Konfiguration"),
    Area("audit_log", "Audit-log", "Kun til arkiv: logs kan ikke importeres.", "Logs", importable=False),
    Area("auth_log", "Auth-log", "Kun til arkiv: logs kan ikke importeres.", "Logs", importable=False),
]
AREA = {a.key: a for a in AREAS}
NEVER_EXPORTED = ("Værdier i Hemmeligheder", "passkeys og sikkerhedsnøgler", "authenticator-apps",
                  "sessioner og engangskoder", "licensnøgle og miljøvariabler", "signerings- og webhook-hemmeligheder")


def area_tables(area: str) -> list[Table]:
    return [t for t in TABLES if t.area == area]


def area_personal(area: str) -> bool:
    return area == "roles" or any(t.personal or t.personal_columns or any(r.table == "users" and r.kind == "owner"
                                                                          for r in t.refs.values())
                                  for t in area_tables(area))


def user_refs(t: Table) -> dict[str, Ref]:
    return {c: r for c, r in t.refs.items() if r.table == "users"}


# ── Options ─────────────────────────────────────────────────────────────────

def clean_export_options(o: dict) -> dict:
    areas = [a for a in dict.fromkeys(o.get("areas") or []) if a in AREA]
    personal = o.get("personal", "pseudo")
    fmt = o.get("format", "json")
    if personal not in PERSONAL_MODES or fmt not in FORMATS:
        raise ValueError("Ugyldige optioner")
    if personal == "without":
        areas = [a for a in areas if a not in ("users", "roles", "audit_log", "auth_log")]
    if "roles" in areas and "users" not in areas:
        areas.remove("roles")
    if not areas:
        raise ValueError("Vælg mindst ét område, der kan komme med")
    since = o.get("since_days")
    since = int(since) if since not in (None, "", 0) else None
    if since is not None and not 1 <= since <= 3650:
        raise ValueError("Perioden skal være mellem 1 og 3650 dage")
    return {"areas": areas, "personal": personal, "format": fmt, "since_days": since,
            "include_blobs": bool(o.get("include_blobs")) and "files" in areas and fmt == "json"}


def clean_import_options(o: dict, manifest: dict) -> dict:
    available = [a for a in manifest.get("areas", []) if a in AREA and AREA[a].importable]
    areas = [a for a in dict.fromkeys(o.get("areas") or []) if a in available]
    if "roles" in areas and "users" not in areas:
        areas.remove("roles")
    if not areas:
        raise ValueError("Vælg mindst ét område")
    out = {"areas": areas,
           "conflict": o.get("conflict", "skip"), "match": o.get("match", "natural"),
           "missing": o.get("missing", "create"), "on_error": o.get("on_error", "all"),
           "keep_times": bool(o.get("keep_times", True)), "file_blobs": bool(o.get("file_blobs", True))}
    if (out["conflict"] not in CONFLICTS or out["match"] not in MATCHES or out["missing"] not in MISSING
            or out["on_error"] not in ON_ERROR):
        raise ValueError("Ugyldige optioner")
    return out


def options_key(options: dict) -> str:
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()


# ── Encryption ──────────────────────────────────────────────────────────────

MAGIC = b"GAERX\x01"
CHUNK = 1 << 20


def _key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def is_encrypted(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(len(MAGIC)) == MAGIC


def encrypt_file(src: str, dst: str, password: str) -> None:
    salt, prefix = os.urandom(16), os.urandom(8)
    aes = AESGCM(_key(password, salt))
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(MAGIC + salt + prefix)
        counter = 0
        chunk = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            last = not nxt
            ct = aes.encrypt(prefix + struct.pack(">I", counter), chunk, b"last" if last else b"")
            fout.write(struct.pack(">I", len(ct)) + ct)
            if last:
                break
            chunk, counter = nxt, counter + 1


def decrypt_file(src: str, dst: str, password: str) -> None:
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        head = fin.read(len(MAGIC) + 24)
        if head[:len(MAGIC)] != MAGIC or len(head) != len(MAGIC) + 24:
            raise PackageError("Pakken er ikke krypteret med Gær")
        salt, prefix = head[len(MAGIC):len(MAGIC) + 16], head[len(MAGIC) + 16:]
        aes = AESGCM(_key(password, salt))
        counter, done = 0, False
        while not done:
            size = fin.read(4)
            if len(size) != 4:
                raise PackageError("Pakken er ufuldstændig")
            ct = fin.read(struct.unpack(">I", size)[0])
            nonce = prefix + struct.pack(">I", counter)
            try:
                fout.write(aes.decrypt(nonce, ct, b""))
            except InvalidTag:
                try:
                    fout.write(aes.decrypt(nonce, ct, b"last"))
                    done = True
                except InvalidTag:
                    raise PackageError("Forkert adgangskode, eller pakken er ændret") from None
            counter += 1
        if fin.read(1):
            raise PackageError("Pakken har data efter slutningen")


# ── Export ──────────────────────────────────────────────────────────────────

def _temp(suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="gaer-transfer-", suffix=suffix)
    os.close(fd)
    return path


def _csv_value(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else v


class _Exporter:
    def __init__(self, conn, options: dict):
        self.conn, self.o = conn, options
        self.manifest_tables: dict = {}
        self.user_ids_seen: set[int] = set()
        self.pseudonyms: dict[int, int] = {}
        self.file_ids: list[str] = []

    async def table_rows(self, t: Table, *, only_ids: set | None = None):
        where = [t.where] if t.where else []
        params: list = []
        if t.time_column and self.o.get("since_days"):
            where.append(f"t.{t.time_column} >= now() - make_interval(days => %s)")
            params.append(self.o["since_days"])
        if only_ids is not None:
            where.append("t.id = any(%s)")
            params.append(sorted(only_ids))
        sql = (f"select to_jsonb(t) as r from {t.name} t" + (" where " + " and ".join(f"({w})" for w in where)
                                                              if where else "") + f" order by {t.order}")
        async with self.conn.cursor(name=f"export_{t.name}") as cur:
            await cur.execute(sql, params)
            while batch := await cur.fetchmany(1000):
                for b in batch:
                    yield b["r"]

    def shape(self, t: Table, raw: dict) -> dict | None:
        """The exported row, or None when it is left out in this personal mode."""
        row = {c: raw.get(c) for c in (*t.pk, *t.columns)}
        mode = self.o["personal"]
        for col, ref in user_refs(t).items():
            v = row.get(col)
            if v is None:
                continue
            if mode == "without":
                if ref.kind == "owner":
                    return None
                row[col] = None
            else:
                self.user_ids_seen.add(int(v))
        if mode != "with":
            for col in t.personal_columns:
                row[col] = None
            if t.name == "users":
                n = self.pseudonyms.setdefault(row["id"], len(self.pseudonyms) + 1)
                row.update(email=f"bruger-{n}@eksport.invalid", phone=None, display_name=f"Bruger {n}")
            elif t.name == "audit_log":
                row.update(ip=None, before=None, after=None, detail={})
            elif t.name == "auth_log":
                row.update(ip=None, phone_masked=None, phone_hash=None, detail={})
        if t.name == "users" and "roles" not in self.o["areas"]:
            row.pop("role", None)
            row.pop("admin_roles", None)
        if t.name == "files":
            self.file_ids.append(row["id"])
        return row

    async def write_table(self, zf: zipfile.ZipFile, t: Table, *, only_ids: set | None = None) -> None:
        ext = self.o["format"]
        name = f"data/{t.name}.{ext}"
        digest, n = hashlib.sha256(), 0
        with zf.open(name, "w", force_zip64=True) as f:
            def out(b: bytes):
                digest.update(b)
                f.write(b)
            writer, buf = None, None
            if ext == "json":
                out(b"[")
            async for raw in self.table_rows(t, only_ids=only_ids):
                row = self.shape(t, raw)
                if row is None:
                    continue
                if ext == "json":
                    out((b",\n" if n else b"\n") + json.dumps(row, ensure_ascii=False, default=str).encode())
                else:
                    if writer is None:
                        buf = io.StringIO()
                        writer = csv.DictWriter(buf, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow({k: _csv_value(v) for k, v in row.items()})
                    out(buf.getvalue().encode())
                    buf.seek(0)
                    buf.truncate()
                n += 1
            if ext == "json":
                out(b"\n]\n")
        self.manifest_tables[t.name] = {"area": t.area, "rows": n, "file": name, "sha256": digest.hexdigest(),
                                        **({"lookup": True} if only_ids is not None or t.lookup_only else {})}


async def build_export(options: dict, path: str) -> dict:
    """Writes the zip to path. Returns the manifest."""
    async with db.connection() as conn:
        await conn.execute("set transaction isolation level repeatable read")
        await conn.execute("set local timezone = 'UTC'")
        ex = _Exporter(conn, options)
        tables = [t for t in TABLES if t.area in options["areas"] or (t.area == "users" and "roles" in options["areas"])]
        blobs = {"count": 0, "bytes": 0}
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for t in tables:
                if t.name != "users":
                    await ex.write_table(zf, t)
            users = TABLE["users"]
            if "users" in options["areas"]:
                await ex.write_table(zf, users)
            elif ex.user_ids_seen and options["personal"] != "without":
                # The users the exported rows belong to, so they can be found (or created) on import.
                await ex.write_table(zf, users, only_ids=ex.user_ids_seen)
            if options.get("include_blobs") and ex.file_ids:
                store = get_storage()
                for r in await db.fetch_all(conn, "select id, storage_key from files where id = any(%s::uuid[])",
                                            (ex.file_ids,)):
                    try:
                        data, _ = await store.get(r["storage_key"])
                    except Exception:  # noqa: BLE001 — a missing object is reported, not fatal
                        log.warning("export: object for file %s is missing", r["id"])
                        continue
                    zf.writestr(f"blobs/{r['id']}", data)
                    blobs["count"] += 1
                    blobs["bytes"] += len(data)
            migration = await db.fetch_one(conn, "select max(name) as name from schema_migrations")
            manifest = {
                "format": FORMAT, "version": VERSION, "kind": options["format"],
                "importable": options["format"] == "json",
                "source": config.settings.origin, "app_version": config.settings.version,
                "migration": migration["name"] if migration else None,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "areas": options["areas"], "personal": options["personal"], "since_days": options.get("since_days"),
                "tables": ex.manifest_tables, "blobs": blobs,
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


# ── Reading a package ───────────────────────────────────────────────────────

def max_bytes() -> int:
    return config.settings.transfer_max_mb * 1024 * 1024


class Package:
    """An opened, verified package (a zip on disk)."""

    def __init__(self, path: str):
        try:
            self.zip = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            raise PackageError("Filen er ikke en Gær-pakke") from None
        names = set(self.zip.namelist())
        if "manifest.json" not in names:
            raise PackageError("Pakken mangler manifest.json")
        if sum(i.file_size for i in self.zip.infolist()) > 4 * max_bytes():
            raise PackageError("Pakken fylder for meget pakket ud")
        try:
            self.manifest = json.loads(self.zip.read("manifest.json"))
        except ValueError:
            raise PackageError("manifest.json kan ikke læses") from None
        m = self.manifest
        if not isinstance(m, dict) or m.get("format") != FORMAT:
            raise PackageError("Filen er ikke en Gær-pakke")
        if not isinstance(m.get("version"), int) or m["version"] > VERSION:
            raise PackageError("Pakken er lavet af en nyere version af Gær")
        if m.get("kind") != "json":
            raise PackageError("En CSV-eksport kan ikke importeres. Eksportér som Gær-pakke.")
        self.tables: dict[str, dict] = {}
        for name, info in (m.get("tables") or {}).items():
            if name not in TABLE or not isinstance(info, dict) or info.get("file") != f"data/{name}.json":
                continue  # a table this version does not know is left out
            if info["file"] not in names:
                raise PackageError(f"Pakken mangler {info['file']}")
            if hashlib.sha256(self.zip.read(info["file"])).hexdigest() != info.get("sha256"):
                raise PackageError(f"Kontrolsummen for {info['file']} stemmer ikke")
            self.tables[name] = info
        self.blob_names = {n.removeprefix("blobs/") for n in names if n.startswith("blobs/")}

    def rows(self, table: str) -> list[dict]:
        if table not in self.tables:
            return []
        rows = json.loads(self.zip.read(self.tables[table]["file"]))
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise PackageError(f"{table} i pakken har ikke rækker")
        return rows

    def blob(self, file_id) -> bytes | None:
        return self.zip.read(f"blobs/{file_id}") if str(file_id) in self.blob_names else None

    def close(self):
        self.zip.close()


def open_package(path: str, password: str | None) -> tuple[Package, list[str]]:
    """Opens the package at path (decrypting it with the password). Returns it and the temp files to remove."""
    temps: list[str] = []
    if is_encrypted(path):
        if not password:
            raise PackageError("Pakken er krypteret. Skriv adgangskoden.")
        plain = _temp(".zip")
        temps.append(plain)
        try:
            decrypt_file(path, plain, password)
        except PackageError:
            os.unlink(plain)
            raise
        path = plain
    try:
        return Package(path), temps
    except PackageError:
        for p in temps:
            os.unlink(p)
        raise


def summary(pkg: Package) -> dict:
    """What the import page shows before the dry run: the areas and their rows."""
    m = pkg.manifest
    areas = []
    for a in AREAS:
        if a.key not in m.get("areas", []):
            continue
        tables = [{"table": t.name, "title": t.title, "rows": pkg.tables[t.name]["rows"]}
                  for t in area_tables(a.key) if t.name in pkg.tables and not t.lookup_only]
        notes = []
        if a.key == "webhooks":
            n = sum(x["rows"] for x in tables)
            if n:
                notes.append(f"{n} oprettes slået fra og uden hemmelighed")
        if a.key == "files" and not pkg.blob_names:
            notes.append("Kun mapper: filernes indhold er ikke med")
        if a.key == "roles":
            notes.append("Kræver Fuld adgang")
        areas.append({"key": a.key, "title": a.title, "importable": a.importable, "personal": area_personal(a.key),
                      "rows": sum(x["rows"] for x in tables), "tables": tables, "notes": notes})
    return {"areas": areas, "blobs": len(pkg.blob_names),
            "lookup_users": bool(pkg.tables.get("users", {}).get("lookup"))}


# ── Import ──────────────────────────────────────────────────────────────────

def _k(values) -> str:
    return json.dumps(list(values), default=str)


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _short(v):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= 120 else s[:117] + "…"


def _db_error(e: psycopg.Error) -> str:
    diag = getattr(e, "diag", None)
    return (diag.message_primary if diag and diag.message_primary else str(e)).strip()[:300]


class _Importer:
    def __init__(self, conn, pkg: Package, options: dict, *, actor_id: int, meta: RequestMeta, transfer_id: int,
                 dry_run: bool):
        self.conn, self.pkg, self.o = conn, pkg, options
        self.actor_id, self.meta, self.transfer_id, self.dry_run = actor_id, meta, transfer_id, dry_run
        self.maps: dict[str, dict[str, tuple]] = {t.name: {} for t in TABLES}
        self.stats: dict[str, dict] = {}
        self.differences: list[dict] = []
        self.errors: list[dict] = []
        self.warnings: dict[tuple[str, str], int] = {}
        self.changes: list[tuple] = []          # (table, row_key, op, before, storage_key)
        self.stored: list[str] = []             # objects written to Object Storage
        self.package_users: dict[str, dict] = {}
        self.after_commit: list[Callable] = []
        self.pending: list[tuple] = []          # changes of the row being written (kept only if it succeeds)
        self.pending_stats: list[tuple] = []
        self.pending_maps: list[tuple] = []
        self.in_row = False
        self.last_outcome = ""
        self.fatal = False

    # Reporting ---------------------------------------------------------------
    def stat(self, t: Table, what: str) -> None:
        if self.in_row and what not in ("skipped", "failed"):
            self.pending_stats.append((t, what))
            self.last_outcome = what
            return
        self.count(t, what)

    def count(self, t: Table, what: str) -> None:
        s = self.stats.setdefault(t.name, {"table": t.name, "title": t.title, "area": t.area, "created": 0,
                                           "updated": 0, "unchanged": 0, "different": 0, "skipped": 0, "failed": 0})
        s[what] += 1

    def warn(self, t: Table, message: str) -> None:
        self.warnings[(t.name, message)] = self.warnings.get((t.name, message), 0) + 1

    def label(self, t: Table, src: dict) -> str:
        if t.personal:
            return f"#{', '.join(str(src.get(c)) for c in t.pk)}"
        return " · ".join(str(src.get(c)) for c in t.key if src.get(c) is not None and c not in t.refs) or \
            f"#{', '.join(str(src.get(c)) for c in t.pk)}"

    # References --------------------------------------------------------------
    async def resolve(self, t: Table, src: dict) -> dict:
        row = dict(src)
        for col, ref in t.refs.items():
            v = row.get(col)
            if v is None:
                continue
            target = self.maps[ref.table].get(_k([v]))
            if target is None and ref.table == "users":
                target = await self.missing_user(t, col, ref, v)
                row[col] = target
                continue
            if target is None:
                target = await self.direct(ref.table, v)
            if target is None:
                if ref.kind == "soft":
                    row[col] = None
                    continue
                if ref.kind == "skip":
                    raise Skip(f"Henviser til {TABLE[ref.table].title.lower()}, som ikke findes her")
                raise RowError(f"Henviser til {TABLE[ref.table].title.lower()} ({v}), som ikke blev importeret")
            row[col] = target[0]
        return row

    async def direct(self, table: str, v):
        """A row this instance has with the same text key (MCP endpoints, settings, features: the key is the id)."""
        t = TABLE[table]
        if t.serial or t.pk != t.key or len(t.pk) != 1:
            return None
        found = await db.fetch_one(self.conn, f"select {t.pk[0]} as k from {t.name} where {t.pk[0]} = %s", (v,))
        return (found["k"],) if found else None

    async def missing_user(self, t: Table, col: str, ref: Ref, v):
        if ref.kind == "soft":
            return None
        mode = self.o["missing"]
        src = self.package_users.get(_k([v]))
        if mode == "me":
            return self.actor_id
        if mode == "create" and src is not None:
            users = TABLE["users"]
            row = {c: src.get(c) for c in users.columns if c not in ("role", "admin_roles", "blocked_at")}
            pk = await self.insert(users, row, src)
            self.stat(users, "created")
            return pk[0]
        raise Skip("Henviser til en bruger, som ikke findes her")

    # Matching ----------------------------------------------------------------
    async def find(self, t: Table, row: dict) -> dict | None:
        if self.o["match"] == "id" and t.serial:
            r = await db.fetch_one(self.conn, f"select to_jsonb(x) as r from {t.name} x where x.id = %s",
                                   (row.get("id"),))
            return r["r"] if r else None
        if t.name == "users":
            if row.get("email"):
                r = await db.fetch_one(self.conn, "select to_jsonb(x) as r from users x where lower(x.email) = lower(%s)",
                                       (row["email"],))
            elif row.get("phone"):
                r = await db.fetch_one(self.conn, "select to_jsonb(x) as r from users x where x.phone = %s",
                                       (row["phone"],))
            else:
                raise RowError("Brugeren har hverken e-mail eller telefon")
            return r["r"] if r else None
        cond = " and ".join(f"x.{c} is not distinct from p.{c}" for c in t.key)
        r = await db.fetch_one(self.conn, f"""select to_jsonb(x) as r from {t.name} x,
                                                  jsonb_populate_record(null::{t.name}, %s) p where {cond} limit 1""",
                               (Jsonb({c: row.get(c) for c in t.key}),))
        return r["r"] if r else None

    # Writing -----------------------------------------------------------------
    def insert_values(self, t: Table, row: dict) -> dict:
        values = {c: row.get(c) for c in (*([] if t.serial else t.pk), *t.columns)}
        if not self.o["keep_times"]:
            for c in (*INSERT_ONLY_TIMES, "updated_at"):
                values.pop(c, None)
            for c in AUTHOR_COLUMNS | EDITOR_COLUMNS:
                if c in values:
                    values[c] = self.actor_id
        if t.name == "users" and "roles" not in self.o["areas"]:
            values.pop("role", None)
            values.pop("admin_roles", None)
        return {c: v for c, v in values.items() if v is not None}

    async def insert(self, t: Table, row: dict, src: dict) -> tuple:
        values = self.insert_values(t, row)
        hook = ON_INSERT.get(t.name)
        storage_key = None
        if hook:
            storage_key = await hook(self, t, values, src)
        cols = ", ".join(values)
        pk = ", ".join(t.pk)
        r = await db.fetch_one(self.conn, f"""insert into {t.name} ({cols})
                                              select {cols} from jsonb_populate_record(null::{t.name}, %s)
                                              returning {pk}""", (Jsonb(values),))
        key = tuple(r[c] for c in t.pk)
        self.map(t, src, key)
        row_key = {c: str(v) if isinstance(v, uuid.UUID) else v for c, v in zip(t.pk, key)}
        self.pending.append((t.name, row_key, "insert", None, storage_key))
        await audit(self.conn, "create", actor_id=self.actor_id, meta=self.meta, table=t.name,
                    row_id=",".join(str(v) for v in key), transfer_id=self.transfer_id, via="import")
        return key

    def map(self, t: Table, src: dict, key: tuple) -> None:
        k = _k(src[c] for c in t.pk)
        self.pending_maps.append((t.name, k, self.maps[t.name].get(k)))
        self.maps[t.name][k] = key

    def update_columns(self, t: Table, existing: dict) -> list[str]:
        cols = [c for c in t.columns if c not in INSERT_ONLY_TIMES | AUTHOR_COLUMNS | EDITOR_COLUMNS
                and c not in t.insert_only and c != "updated_at"]
        if t.name == "users":
            cols = [c for c in cols if c not in ("role", "admin_roles")]
            if "roles" in self.o["areas"] and existing.get("id") != self.actor_id and not existing.get("super_admin"):
                cols += ["role", "admin_roles"]
            if existing.get("id") == self.actor_id:
                cols = [c for c in cols if c != "blocked_at"]
        return cols

    async def on_existing(self, t: Table, row: dict, src: dict, existing: dict) -> None:
        key = tuple(existing[c] for c in t.pk)
        cols = [c for c in self.update_columns(t, existing) if c in row]
        differs = [c for c in cols if row.get(c) != existing.get(c)]
        conflict = self.o["conflict"]
        if conflict == "copy" and t.copy:
            copy = dict(row)
            for col, how in t.copy.items():
                copy[col] = await self.free_value(t, copy, col, how)
            await self.insert(t, copy, src)
            self.stat(t, "created")
            return
        self.map(t, src, key)
        if not differs:
            self.stat(t, "unchanged")
            return
        if conflict in ("skip", "copy") or not t.updatable:
            self.stat(t, "different")
            self.difference(t, src, differs, existing, row)
            return
        if conflict == "merge":
            differs = [c for c in differs if _empty(existing.get(c)) and not _empty(row.get(c))]
            if not differs:
                self.stat(t, "different")
                self.difference(t, src, [c for c in cols if row.get(c) != existing.get(c)], existing, row)
                return
        new = {c: row.get(c) for c in differs}
        if "updated_at" in t.columns:
            new["updated_at"] = row.get("updated_at") if self.o["keep_times"] and row.get("updated_at") else \
                datetime.now(timezone.utc).isoformat()
        if "updated_by" in t.columns:
            new["updated_by"] = self.actor_id
        sets = ", ".join(new)
        where = " and ".join(f"{c} = %s" for c in t.pk)
        await self.conn.execute(f"""update {t.name} set ({sets}) = (select {sets}
                                     from jsonb_populate_record(null::{t.name}, %s)) where {where}""",
                                (Jsonb(new), *key))
        before = {c: existing.get(c) for c in new}
        self.pending.append((t.name, {c: str(v) if isinstance(v, uuid.UUID) else v for c, v in zip(t.pk, key)},
                             "update", before, None))
        await audit(self.conn, "update", actor_id=self.actor_id, meta=self.meta, table=t.name,
                    row_id=",".join(str(v) for v in key), before=None if t.personal else _audit_values(before, differs),
                    after=None if t.personal else _audit_values(new, differs), fields=differs,
                    transfer_id=self.transfer_id, via="import")
        self.stat(t, "updated")

    async def free_value(self, t: Table, row: dict, col: str, how: str) -> str:
        base = row.get(col) or ""
        for n in range(1, 100):
            candidate = (f"{base}-import" + (f"-{n}" if n > 1 else "")) if how == "slug" else \
                (f"{base} (importeret)" + (f" {n}" if n > 1 else ""))
            trial = {**row, col: candidate}
            if col not in t.key or await self.find_natural(t, trial) is None:
                return candidate
        raise RowError("Kunne ikke finde et ledigt navn til kopien")

    async def find_natural(self, t: Table, row: dict):
        match, self.o["match"] = self.o["match"], "natural"
        try:
            return await self.find(t, row)
        finally:
            self.o["match"] = match

    def difference(self, t: Table, src: dict, fields: list[str], existing: dict, row: dict) -> None:
        if len(self.differences) >= MAX_DIFFERENCES:
            return
        item = {"table": t.name, "title": t.title, "key": self.label(t, src), "fields": fields}
        if not t.personal:
            item["here"] = {c: _short(existing.get(c)) for c in fields[:5]}
            item["package"] = {c: _short(row.get(c)) for c in fields[:5]}
        self.differences.append(item)

    # The run -----------------------------------------------------------------
    def needed_tables(self) -> list[Table]:
        write = {t.name for t in TABLES if t.area in self.o["areas"] and t.importable and not t.lookup_only}
        if "roles" in self.o["areas"]:
            write.add("users")
        needed = set(write)
        for name in list(write):
            for ref in TABLE[name].refs.values():
                needed.add(ref.table)
        return [t for t in TABLES if t.name in needed and t.name in self.pkg.tables]

    async def run(self) -> dict:
        await self.conn.execute("set local timezone = 'UTC'")
        write = {t.name for t in TABLES if t.area in self.o["areas"] and t.importable and not t.lookup_only}
        for t in self.needed_tables():
            rows = self.pkg.rows(t.name)
            if t.name == "users":
                self.package_users = {_k([r.get("id")]): r for r in rows}
            if t.name in write:
                for src in rows:
                    await self.write_row(t, src)
            else:
                for src in rows:
                    await self.lookup_row(t, src)
        if "users" in write or "roles" in self.o["areas"]:
            n = await db.fetch_one(self.conn, """select count(*) as n from users where role = 'admin'
                                                 and blocked_at is null and not impersonation_only""")
            if n["n"] < 1:
                self.fatal = True
                self.errors.append({"table": "users", "title": "Brugere", "key": "",
                                    "message": "Importen ville efterlade instansen uden en aktiv admin"})
        return self.report()

    async def lookup_row(self, t: Table, src: dict) -> None:
        try:
            async with self.conn.transaction():
                row = await self.resolve_lookup(t, src)
                existing = await self.find(t, row) if row is not None else None
        except (RowError, Skip, psycopg.Error):
            return
        if existing:
            self.maps[t.name][_k(src[c] for c in t.pk)] = tuple(existing[c] for c in t.pk)

    async def resolve_lookup(self, t: Table, src: dict) -> dict | None:
        row = dict(src)
        for col, ref in t.refs.items():
            v = row.get(col)
            if v is None:
                continue
            target = self.maps[ref.table].get(_k([v])) or await self.direct(ref.table, v)
            if target is None:
                if ref.kind == "soft":
                    row[col] = None
                    continue
                return None
            row[col] = target[0]
        return row

    async def write_row(self, t: Table, src: dict) -> None:
        self.pending, self.pending_stats, self.pending_maps = [], [], []
        self.in_row, self.last_outcome = True, ""
        stored_before = len(self.stored)
        try:
            async with self.conn.transaction():
                row = await self.resolve(t, src)
                hook = PREPARE.get(t.name)
                if hook:
                    await hook(self, t, row)
                existing = await self.find(t, row)
                if existing is None:
                    await self.insert(t, row, src)
                    self.stat(t, "created")
                else:
                    await self.on_existing(t, row, src, existing)
                after = AFTER_WRITE.get(t.name)
                if after:
                    await after(self, t, row, src)
        except Skip as s:
            self.discard(stored_before)
            self.count(t, "skipped")
            self.warn(t, str(s))
            return
        except (RowError, psycopg.Error, ValueError) as e:
            self.discard(stored_before)
            self.count(t, "failed")
            if len(self.errors) < MAX_ERRORS:
                self.errors.append({"table": t.name, "title": t.title, "key": self.label(t, src),
                                    "message": _db_error(e) if isinstance(e, psycopg.Error) else str(e)})
            return
        finally:
            self.in_row = False
        self.changes.extend(self.pending)
        for tbl, what in self.pending_stats:
            self.count(tbl, what)

    def discard(self, stored_before: int) -> None:
        """A row that failed: its references are forgotten, and objects it stored go again."""
        self.in_row = False
        for table, k, old in reversed(self.pending_maps):
            if old is None:
                self.maps[table].pop(k, None)
            else:
                self.maps[table][k] = old
        for key in self.stored[stored_before:]:
            self.after_commit.append(lambda k=key: get_storage().delete(k))
        del self.stored[stored_before:]

    def report(self) -> dict:
        tables = [self.stats[t.name] for t in TABLES if t.name in self.stats]
        totals = {k: sum(s[k] for s in tables) for k in ("created", "updated", "unchanged", "different", "skipped",
                                                         "failed")}
        totals["errors"] = len(self.errors)
        warnings = [{"table": t, "title": TABLE[t].title, "message": m, "count": n}
                    for (t, m), n in sorted(self.warnings.items())]
        return {"dry_run": self.dry_run, "options": self.o, "tables": tables, "totals": totals,
                "differences": self.differences, "errors": self.errors, "warnings": warnings,
                "blocked": self.fatal or (bool(self.errors) and self.o["on_error"] == "all"),
                "finished_at": datetime.now(timezone.utc).isoformat()}


def _audit_values(values: dict, fields: list[str]) -> dict:
    """Changed fields for the audit log; long text only as its length."""
    out = {}
    for c in fields:
        v = values.get(c)
        out[c] = f"<{len(v)} tegn>" if isinstance(v, str) and len(v) > 200 else v
    return out


# Table hooks -------------------------------------------------------------------

async def _prepare_settings(imp: _Importer, t: Table, row: dict) -> None:
    if row.get("key") not in SETTINGS_KEYS:
        raise Skip("Indstillingen findes ikke i denne version")


async def _prepare_endpoint(imp: _Importer, t: Table, row: dict) -> None:
    from app import webhooks_in
    if row.get("handler") not in webhooks_in.HANDLERS:
        raise RowError(f"Handleren {row.get('handler')} findes ikke i denne version")


async def _prepare_source(imp: _Importer, t: Table, row: dict) -> None:
    from app import data_sources
    try:
        spec = data_sources.parse_spec(row.get("spec_yaml") or "")
    except data_sources.SpecError as e:
        raise RowError(f"Kildebeskrivelsen er ugyldig: {e}") from None
    if spec["key"] != row.get("key"):
        raise RowError("Kildens navn passer ikke med beskrivelsen")
    row.update(name=spec["name"], kind=spec["kind"], hosts=spec["hosts"])
    row["_spec"] = spec
    if spec["secrets"]:
        imp.warn(t, "Bruger hemmeligheder, der skal findes her med samme navn")


async def _after_source(imp: _Importer, t: Table, row: dict, src: dict) -> None:
    from app.routes.data import _sync
    key = imp.maps[t.name].get(_k([src["id"]]))
    if key is not None and row.get("_spec") and imp.last_outcome in ("created", "updated"):
        await _sync(imp.conn, key[0], row["_spec"])


async def _prepare_journey(imp: _Importer, t: Table, row: dict) -> None:
    aud = row.get("audience") or {}
    if isinstance(aud, dict) and aud.get("kind") == "users":
        ids = [imp.maps["users"].get(_k([i])) for i in aud.get("user_ids") or []]
        mapped = sorted({i[0] for i in ids if i})
        if len(mapped) < len(ids):
            imp.warn(t, "Brugere i målgruppen, som ikke findes her, er taget ud")
        if mapped:
            row["audience"] = {"kind": "users", "user_ids": mapped}
        else:
            row["audience"] = {"kind": "all"}
            row["status"] = "draft"
            imp.warn(t, "Målgruppen var tom og er sat til alle; forløbet er en kladde")


async def _prepare_file(imp: _Importer, t: Table, row: dict) -> None:
    if row.get("space") == "user" and row.get("user_id") is None:
        raise Skip("Filen ligger i en hjemmemappe uden ejer")


async def _insert_webhook(imp: _Importer, t: Table, values: dict, src: dict) -> None:
    values["secret_enc"] = "\\x" + secrets_box.encrypt(secrets.token_urlsafe(32), "webhook-secret").hex()
    values["active"] = False
    values["disabled_reason"] = "Importeret: ny signeringsnøgle, slå til igen efter kontrol"
    imp.warn(t, "Oprettet slået fra med en ny signeringsnøgle")


async def _insert_endpoint(imp: _Importer, t: Table, values: dict, src: dict) -> None:
    if values.get("verify", "gaer") != "none":
        values["active"] = False
        imp.warn(t, "Oprettet slået fra: hemmeligheden skal sættes igen")


async def _insert_file(imp: _Importer, t: Table, values: dict, src: dict) -> str | None:
    data = imp.pkg.blob(src.get("id")) if imp.o["file_blobs"] else None
    if data is None:
        raise Skip("Filens indhold er ikke med i pakken")
    prefix = f"users/{values['user_id']}" if values.get("space") == "user" else "shared"
    file_id = uuid.uuid4()
    key = f"{prefix}/{file_id}"
    values["id"] = str(file_id)
    values["storage_key"] = key
    values["size_bytes"] = len(data)
    values["kind"] = "file"
    if not imp.dry_run:
        await get_storage().put(key, data, values.get("content_type") or "application/octet-stream")
        imp.stored.append(key)
    return key


PREPARE = {"settings": _prepare_settings, "webhook_endpoints": _prepare_endpoint, "data_sources": _prepare_source,
           "adoption_journeys": _prepare_journey, "files": _prepare_file}
ON_INSERT = {"webhooks": _insert_webhook, "webhook_endpoints": _insert_endpoint, "files": _insert_file}
AFTER_WRITE = {"data_sources": _after_source}


async def apply_package(pkg: Package, options: dict, *, actor_id: int, transfer_id: int, dry_run: bool) -> dict:
    """Runs the import in one transaction. A dry run, or a run with errors and on_error 'all', is rolled back."""
    meta = RequestMeta()
    imp = None
    async with db.connection() as conn:
        imp = _Importer(conn, pkg, dict(options), actor_id=actor_id, meta=meta, transfer_id=transfer_id,
                        dry_run=dry_run)
        report = await imp.run()
        if dry_run or report["blocked"]:
            await conn.rollback()
            for key in imp.stored:
                imp.after_commit.append(lambda k=key: get_storage().delete(k))
            report["rolled_back"] = not dry_run
        else:
            if imp.changes:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """insert into data_transfer_changes (transfer_id, table_name, row_key, op, before, storage_key)
                           values (%s, %s, %s, %s, %s, %s)""",
                        [(transfer_id, tbl, Jsonb(k), op, Jsonb(b) if b is not None else None, sk)
                         for tbl, k, op, b, sk in imp.changes])
            await audit(conn, "transfer.imported", actor_id=actor_id, meta=meta, table="data_transfers",
                        row_id=transfer_id, totals=report["totals"], areas=options["areas"])
            await webhooks.emit(conn, "data.imported", {"id": transfer_id, "created": report["totals"]["created"],
                                                        "updated": report["totals"]["updated"]})
            report["changes"] = len(imp.changes)
    for fn in imp.after_commit:
        try:
            await fn()
        except Exception:  # noqa: BLE001
            log.warning("transfer: cleanup of a stored object failed")
    return report


# ── Undo ────────────────────────────────────────────────────────────────────

async def undo(transfer_id: int, actor_id: int | None) -> dict:
    meta = RequestMeta()
    restored = deleted = failed = 0
    errors: list[dict] = []
    objects: list[str] = []
    async with db.connection() as conn:
        await conn.execute("set local timezone = 'UTC'")
        changes = await db.fetch_all(conn, """select * from data_transfer_changes where transfer_id = %s
                                              order by id desc""", (transfer_id,))
        sources = []
        for ch in changes:
            t = TABLE.get(ch["table_name"])
            if t is None:
                continue
            key = ch["row_key"]
            where = " and ".join(f"{c} = %s" for c in t.pk)
            params = [key[c] for c in t.pk]
            try:
                async with conn.transaction():
                    if ch["op"] == "insert":
                        await conn.execute(f"delete from {t.name} where {where}", params)
                        if ch["storage_key"]:
                            objects.append(ch["storage_key"])
                        await audit(conn, "delete", actor_id=actor_id, meta=meta, table=t.name,
                                    row_id=",".join(str(v) for v in params), transfer_id=transfer_id, via="undo")
                        deleted += 1
                    else:
                        before = ch["before"] or {}
                        sets = ", ".join(before)
                        await conn.execute(f"""update {t.name} set ({sets}) = (select {sets}
                                               from jsonb_populate_record(null::{t.name}, %s)) where {where}""",
                                           (Jsonb(before), *params))
                        await audit(conn, "update", actor_id=actor_id, meta=meta, table=t.name,
                                    row_id=",".join(str(v) for v in params), fields=list(before),
                                    transfer_id=transfer_id, via="undo")
                        if t.name == "data_sources" and "spec_yaml" in before:
                            sources.append(params[0])
                        restored += 1
            except psycopg.Error as e:
                failed += 1
                if len(errors) < MAX_ERRORS:
                    errors.append({"table": t.name, "title": t.title, "key": ",".join(str(v) for v in params),
                                   "message": _db_error(e)})
        if sources:
            from app import data_sources
            from app.routes.data import _sync
            for sid in sources:
                row = await db.fetch_one(conn, "select spec_yaml from data_sources where id = %s", (sid,))
                if row:
                    await _sync(conn, sid, data_sources.parse_spec(row["spec_yaml"]))
        await conn.execute("delete from data_transfer_changes where transfer_id = %s", (transfer_id,))
        await audit(conn, "transfer.undone", actor_id=actor_id, meta=meta, table="data_transfers",
                    row_id=transfer_id, deleted=deleted, restored=restored, failed=failed)
        await webhooks.emit(conn, "data.import.undone", {"id": transfer_id})
    for key in objects:
        try:
            await get_storage().delete(key)
        except Exception:  # noqa: BLE001
            log.warning("transfer: object of an undone import could not be deleted")
    return {"deleted": deleted, "restored": restored, "failed": failed, "errors": errors}


# ── Jobs ────────────────────────────────────────────────────────────────────

async def _transfer(conn, transfer_id: int, *, lock: bool = False) -> dict | None:
    return await db.fetch_one(conn, "select * from data_transfers where id = %s" + (" for update" if lock else ""),
                              (transfer_id,))


async def _set(transfer_id: int, **values) -> None:
    sets = ", ".join(f"{k} = %s" for k in values)
    async with db.connection() as conn:
        await conn.execute(f"update data_transfers set {sets}, updated_at = now() where id = %s",
                           (*[Jsonb(v) if isinstance(v, (dict, list)) else v for v in values.values()], transfer_id))


def _password(row: dict) -> str | None:
    return secrets_box.decrypt(row["password_enc"], PASSWORD_PURPOSE) if row.get("password_enc") else None


@jobs.handler("data.export")
async def export_job(payload: dict, job: dict) -> dict:
    tid = payload["transfer_id"]
    async with db.connection() as conn:
        row = await _transfer(conn, tid, lock=True)
        if row is None or row["status"] != "queued":
            return {"skipped": True}
        await conn.execute("update data_transfers set status = 'running', updated_at = now() where id = %s", (tid,))
    plain, sealed = _temp(".zip"), None
    try:
        manifest = await build_export(row["options"], plain)
        path = plain
        password = _password(row)
        if password:
            sealed = _temp(".gaerx")
            encrypt_file(plain, sealed, password)
            path = sealed
        size = os.path.getsize(path)
        if size > max_bytes():
            raise PackageError(f"Pakken blev større end {config.settings.transfer_max_mb} MB. Vælg færre områder "
                               "eller udelad filernes indhold.")
        key = f"transfers/{uuid.uuid4()}"
        with open(path, "rb") as f:
            await get_storage().put_stream(key, f, "application/zip" if not password else "application/octet-stream")
        async with db.connection() as conn:
            current = await _transfer(conn, tid, lock=True)
            if current["status"] == "cancelled":
                await get_storage().delete(key)
                return {"cancelled": True}
            await conn.execute("""update data_transfers set status = 'ready', storage_key = %s, size_bytes = %s,
                                  manifest = %s, password_enc = null, finished_at = now(), updated_at = now(),
                                  expires_at = now() + make_interval(hours => %s) where id = %s""",
                               (key, size, Jsonb(manifest), DOWNLOAD_HOURS, tid))
            rows = sum(t["rows"] for t in manifest["tables"].values())
            await audit(conn, "transfer.exported", actor_id=row["created_by"], meta=RequestMeta(),
                        table="data_transfers", row_id=tid, areas=manifest["areas"], personal=manifest["personal"],
                        rows=rows, files=manifest["blobs"]["count"], encrypted=bool(password), bytes=size)
            await webhooks.emit(conn, "data.exported", {"id": tid, "areas": manifest["areas"], "rows": rows})
        await events.emit("transfer.exported", ref=str(tid), value=size)
        return {"rows": rows, "bytes": size}
    except Exception as e:  # noqa: BLE001 — recorded on the transfer, not retried
        log.warning("export %s failed: %s", tid, e)
        await _set(tid, status="failed", error=str(e)[:500] if isinstance(e, PackageError) else
                   "Eksporten fejlede. Se jobbet i Jobs og oprydning.", password_enc=None, finished_at=_now())
        return {"error": str(e)[:500]}
    finally:
        for p in (plain, sealed):
            if p and os.path.exists(p):
                os.unlink(p)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _download(row: dict) -> str:
    path = _temp(".pkg")
    await get_storage().download_to(row["storage_key"], path)
    return path


@jobs.handler("data.import")
async def import_job(payload: dict, job: dict) -> dict:
    tid, dry_run = payload["transfer_id"], bool(payload.get("dry_run"))
    async with db.connection() as conn:
        row = await _transfer(conn, tid, lock=True)
        expected = "dry_running" if dry_run else "running"
        if row is None or row["status"] != expected:
            return {"skipped": True}
    options = payload["options"]
    temps: list[str] = []
    pkg = None
    try:
        path = await _download(row)
        temps.append(path)
        pkg, more = open_package(path, _password(row))
        temps += more
        report = await apply_package(pkg, options, actor_id=payload.get("actor_id") or row["created_by"],
                                     transfer_id=tid, dry_run=dry_run)
        async with db.connection() as conn:
            if dry_run:
                await conn.execute("""update data_transfers set status = 'dry_ready', report = %s, options = %s,
                                      dry_run_key = %s, error = null, updated_at = now() where id = %s""",
                                   (Jsonb(report), Jsonb(options), options_key(options), tid))
                await audit(conn, "transfer.dry_run", actor_id=payload.get("actor_id") or row["created_by"],
                            meta=RequestMeta(),
                            table="data_transfers", row_id=tid, totals=report["totals"])
            elif report["blocked"]:
                await conn.execute("""update data_transfers set status = 'dry_ready', report = %s, dry_run_key = null,
                                      error = %s, updated_at = now() where id = %s""",
                                   (Jsonb(report), "Importen fandt fejl og blev rullet tilbage", tid))
            else:
                await conn.execute("""update data_transfers set status = 'done', report = %s, finished_at = now(),
                                      password_enc = null, updated_at = now(),
                                      undo_until = case when %s > 0 then now() + make_interval(days => %s) end
                                      where id = %s""", (Jsonb(report), report.get("changes", 0), UNDO_DAYS, tid))
        if not dry_run and not report["blocked"]:
            await events.emit("transfer.imported", ref=str(tid), value=report["totals"]["created"])
        return {"totals": report["totals"], "dry_run": dry_run}
    except Exception as e:  # noqa: BLE001
        log.warning("import %s failed: %s", tid, e)
        message = str(e) if isinstance(e, PackageError) else "Importen fejlede. Se jobbet i Jobs og oprydning."
        await _set(tid, status="uploaded" if dry_run else "failed", error=message[:500])
        return {"error": str(e)[:500]}
    finally:
        if pkg:
            pkg.close()
        for p in temps:
            if os.path.exists(p):
                os.unlink(p)


@jobs.handler("data.undo")
async def undo_job(payload: dict, job: dict) -> dict:
    tid = payload["transfer_id"]
    async with db.connection() as conn:
        row = await _transfer(conn, tid, lock=True)
        if row is None or row["status"] != "undoing":
            return {"skipped": True}
    try:
        result = await undo(tid, payload.get("actor_id"))
    except Exception as e:  # noqa: BLE001
        log.warning("undo %s failed: %s", tid, e)
        await _set(tid, status="done", error="Fortrydelsen fejlede og blev rullet tilbage")
        return {"error": str(e)[:500]}
    report = dict(row["report"] or {})
    report["undo"] = result
    await _set(tid, status="undone", report=report, undo_until=None, error=None)
    return result


@jobs.handler("data.transfer_cleanup")
async def cleanup_job(payload: dict, job: dict) -> dict:
    """Deletes export packages after DOWNLOAD_HOURS, uploaded packages after IMPORT_PACKAGE_DAYS and the undo data of
    imports after UNDO_DAYS."""
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select id, storage_key from data_transfers
            where storage_key is not null and status not in ('queued', 'running', 'dry_running', 'undoing')
              and ((kind = 'export' and coalesce(expires_at, created_at) < now())
                   or (kind = 'import' and created_at < now() - make_interval(days => %s)))
            for update skip locked""", (IMPORT_PACKAGE_DAYS,))
        if rows:
            await conn.execute("""
                update data_transfers set storage_key = null, password_enc = null, updated_at = now(),
                       status = case when status in ('ready', 'uploaded', 'dry_ready') then 'expired' else status end
                where id = any(%s)""", ([r["id"] for r in rows],))
        undo_rows = await db.fetch_all(conn, """update data_transfers set undo_until = null, updated_at = now()
                                                where undo_until < now() returning id""")
        if undo_rows:
            await conn.execute("delete from data_transfer_changes where transfer_id = any(%s)",
                               ([r["id"] for r in undo_rows],))
    await delete_package_objects([r["storage_key"] for r in rows])
    return {"packages": len(rows), "undo_windows": len(undo_rows)}


async def delete_package_objects(keys: list[str]) -> None:
    for key in keys:
        try:
            await get_storage().delete(key)
        except Exception:  # noqa: BLE001
            log.warning("transfer: package object could not be deleted")
