"""The mail server behind the module: Stalwart in production, an in-memory stand-in in development and tests.

Everything the app asks of the mail server goes through one of these classes, so the rest of the module never
builds a Stalwart request itself. Every write is idempotent and keyed by principal name (and collection slug), so a
failed call can simply be retried.

Stalwart is reached on the private network only (STALWART_URL):
  management API  /api/principal, /api/queue, /api/settings   STALWART_API_KEY ("<user>:<secret>" or a bearer token)
  JMAP            /.well-known/jmap                           as an account, via the master user
  WebDAV          /dav/cal/<account>/…, /dav/card/<account>/…  as an account, via the master user
The master user ("<account>%<STALWART_MASTER_USER>" + STALWART_MASTER_SECRET) lets the backend act as an account
without knowing its password. Admins never see content: the backend only creates, renames, shares, counts, exports
(backups, the user's own archive download) and expires.

Mapping of the spec's roles onto Stalwart:
  shared mailbox     a group principal; sender and manager members are group members (they may send as the address),
                     readers get the group's mailboxes shared over JMAP (read, file) without the right to send
  calendars, books   WebDAV collections; viewer = read (and free/busy), editor = read + write, manager = all
  app passwords      principal secrets named "$app$<name>$<secret>"

Check the calls against the Stalwart version you deploy (specs/mail/README.md lists what to verify).
"""

import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

from app import config

CALENDAR, ADDRESS_BOOK = "calendar", "address_book"
DAV_ROOT = {CALENDAR: "cal", ADDRESS_BOOK: "card"}
COLLECTION_PRIVILEGES = {"viewer": ["read"], "editor": ["read", "write"], "manager": ["all"]}


class MailBackendError(RuntimeError):
    """The mail server failed or is not reachable. Shown to admins as is, never with credentials."""


class NotConfigured(MailBackendError):
    pass


def split_cards(text: str) -> list[tuple[str, str]]:
    """Splits a .vcf file into (uid, card). A card without UID gets one, so an import can be repeated safely."""
    out = []
    for m in re.finditer(r"BEGIN:VCARD.*?END:VCARD", text, flags=re.S | re.I):
        card = m.group(0).replace("\r\n", "\n").strip()
        uid = re.search(r"^UID[^:\n]*:(.+)$", card, flags=re.M | re.I)
        if uid:
            key = uid.group(1).strip()
        else:
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, card))
            card = card.replace("END:VCARD", f"UID:{key}\nEND:VCARD")
        out.append((re.sub(r"[^A-Za-z0-9._-]", "_", key)[:120] or uuid.uuid4().hex, card.replace("\n", "\r\n") + "\r\n"))
    return out


def join_calendars(items: list[str], name: str) -> bytes:
    """One .ics from the calendar objects of a collection (each its own VCALENDAR)."""
    timezones: dict[str, str] = {}
    components: list[str] = []
    for item in items:
        text = item.replace("\r\n", "\n")
        for m in re.finditer(r"BEGIN:(VTIMEZONE|VEVENT|VTODO|VJOURNAL)\n.*?END:\1", text, flags=re.S):
            block = m.group(0)
            if m.group(1) == "VTIMEZONE":
                tzid = re.search(r"^TZID:(.+)$", block, flags=re.M)
                timezones.setdefault(tzid.group(1) if tzid else block, block)
            else:
                components.append(block)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Gaer//Mail backup//DA", f"X-WR-CALNAME:{name}",
             *timezones.values(), *components, "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode()


# ── In memory (development and tests) ───────────────────────────────────────

@dataclass
class MemMessage:
    id: str
    received_at: datetime
    folder: str            # inbox, sent, trash, junk, …
    raw: bytes


@dataclass
class MemCollection:
    name: str
    colour: str | None = None
    timezone: str | None = None
    items: dict[str, str] = field(default_factory=dict)   # href → iCalendar / vCard
    access: dict[str, str] = field(default_factory=dict)  # principal → viewer | editor | manager
    freebusy: bool = True


class MemoryBackend:
    """Behaves like Stalwart for the module's purposes. State lives in the process."""

    name = "memory"

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.principals: dict[str, dict] = {}
        self.collections: dict[tuple[str, str, str], MemCollection] = {}
        self.messages: dict[str, list[MemMessage]] = {}
        self.identities: dict[tuple[str, str], dict] = {}
        self.availability: dict[str, dict] = {}
        self.queue = {"paused": False, "messages": 0, "oldest": None}
        self.relay: dict = {}
        self.calls: list[tuple] = []   # tests read what was asked
        self.fail: str | None = None   # tests: raise on the next call whose name contains this

    def _call(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        if self.fail and self.fail in name:
            self.fail = None
            raise MailBackendError(f"{name} fejlede (test)")

    # Principals
    async def ensure_account(self, name: str, *, emails: list[str], quota_mb: int, description: str) -> None:
        self._call("ensure_account", name)
        p = self.principals.setdefault(name, {"type": "individual", "secrets": [], "member_of": [], "enabled": True,
                                              "used_bytes": 0})
        p.update(emails=list(emails), quota_mb=quota_mb, description=description)

    async def ensure_group(self, name: str, *, emails: list[str], description: str) -> None:
        self._call("ensure_group", name)
        p = self.principals.setdefault(name, {"type": "group", "members": {}, "enabled": True, "used_bytes": 0})
        p.update(emails=list(emails), description=description)

    async def update_principal(self, name: str, *, quota_mb: int | None = None, enabled: bool | None = None,
                               emails: list[str] | None = None, description: str | None = None) -> None:
        self._call("update_principal", name)
        p = self.principals.get(name)
        if p is None:
            raise MailBackendError(f"principal {name} findes ikke i Stalwart")
        for key, value in (("quota_mb", quota_mb), ("enabled", enabled), ("emails", emails), ("description", description)):
            if value is not None:
                p[key] = list(value) if isinstance(value, list) else value

    async def delete_principal(self, name: str) -> None:
        self._call("delete_principal", name)
        self.principals.pop(name, None)
        self.messages.pop(name, None)
        for key in [k for k in self.collections if k[0] == name]:
            del self.collections[key]
        for p in self.principals.values():
            p.get("members", {}).pop(name, None)

    async def principal(self, name: str) -> dict | None:
        self._call("principal", name)
        p = self.principals.get(name)
        return None if p is None else {"name": name, **{k: v for k, v in p.items() if k != "secrets"}}

    async def list_principals(self) -> list[str]:
        self._call("list_principals")
        return sorted(self.principals)

    async def set_group_members(self, group: str, members: dict[str, str]) -> None:
        self._call("set_group_members", group)
        p = self.principals.get(group)
        if p is None:
            raise MailBackendError(f"gruppen {group} findes ikke i Stalwart")
        p["members"] = dict(members)

    # Credentials
    async def add_app_password(self, account: str, secret_name: str, password: str) -> None:
        self._call("add_app_password", account, secret_name)
        p = self.principals[account]
        p["secrets"] = [s for s in p["secrets"] if not s.startswith(f"$app${secret_name}$")]
        p["secrets"].append(f"$app${secret_name}${password}")

    async def remove_app_password(self, account: str, secret_name: str) -> None:
        self._call("remove_app_password", account, secret_name)
        p = self.principals.get(account)
        if p:
            p["secrets"] = [s for s in p["secrets"] if not s.startswith(f"$app${secret_name}$")]

    async def set_identity(self, account: str, address: str, display_name: str, signature: str) -> None:
        self._call("set_identity", account, address)
        self.identities[(account, address)] = {"name": display_name, "signature": signature}

    # Calendars and address books
    def _col(self, owner: str, type: str, slug: str) -> MemCollection:
        c = self.collections.get((owner, type, slug))
        if c is None:
            raise MailBackendError(f"{type} {owner}/{slug} findes ikke i Stalwart")
        return c

    async def ensure_collection(self, owner: str, type: str, slug: str, *, name: str, colour: str | None = None,
                                timezone: str | None = None) -> None:
        self._call("ensure_collection", owner, type, slug)
        c = self.collections.setdefault((owner, type, slug), MemCollection(name))
        c.name, c.colour, c.timezone = name, colour, timezone

    async def delete_collection(self, owner: str, type: str, slug: str) -> None:
        self._call("delete_collection", owner, type, slug)
        self.collections.pop((owner, type, slug), None)

    async def set_collection_access(self, owner: str, type: str, slug: str, grants: dict[str, str]) -> None:
        self._call("set_collection_access", owner, type, slug)
        self._col(owner, type, slug).access = dict(grants)

    async def set_freebusy(self, owner: str, slug: str, opaque: bool) -> None:
        self._call("set_freebusy", owner, slug)
        self._col(owner, CALENDAR, slug).freebusy = opaque

    async def collection_items(self, owner: str, type: str, slug: str) -> dict[str, str]:
        self._call("collection_items", owner, type, slug)
        return dict(self._col(owner, type, slug).items)

    async def put_items(self, owner: str, type: str, slug: str, items: dict[str, str]) -> int:
        self._call("put_items", owner, type, slug)
        self._col(owner, type, slug).items.update(items)
        return len(items)

    async def set_availability(self, account: str, *, publish: bool, working_hours: dict, tz: str) -> None:
        self._call("set_availability", account)
        self.availability[account] = {"publish": publish, "working_hours": working_hours, "timezone": tz}

    # Mail (retention and archive export only; never shown to anyone but the account owner)
    def add_message(self, account: str, received_at: datetime, folder: str = "inbox", raw: bytes = b"") -> str:
        mid = uuid.uuid4().hex
        self.messages.setdefault(account, []).append(
            MemMessage(mid, received_at, folder, raw or b"Subject: test\r\n\r\nHej\r\n"))
        return mid

    async def messages_before(self, account: str, before: datetime, applies_to: str) -> list[str]:
        self._call("messages_before", account)
        return [m.id for m in self.messages.get(account, [])
                if m.received_at < before and (applies_to == "all" or m.folder == applies_to)]

    async def raw_messages(self, account: str, ids: list[str]) -> list[tuple[str, bytes]]:
        self._call("raw_messages", account)
        wanted = set(ids)
        return [(m.id, m.raw) for m in self.messages.get(account, []) if m.id in wanted]

    async def destroy_messages(self, account: str, ids: list[str]) -> int:
        self._call("destroy_messages", account)
        before = len(self.messages.get(account, []))
        gone = set(ids)
        self.messages[account] = [m for m in self.messages.get(account, []) if m.id not in gone]
        return before - len(self.messages[account])

    # Operations
    async def health(self) -> dict:
        self._call("health")
        return {"reachable": True, "version": "memory", "uptime_seconds": None,
                "queue": {"messages": self.queue["messages"], "oldest_at": self.queue["oldest"],
                          "paused": self.queue["paused"]},
                "stores": {"data": True, "blob": True}}

    async def queue_action(self, action: str) -> None:
        self._call("queue_action", action)
        if action in ("pause", "resume"):
            self.queue["paused"] = action == "pause"
        elif action == "flush":
            self.queue["messages"], self.queue["oldest"] = 0, None

    async def set_relay_credentials(self, username: str, password: str) -> None:
        self._call("set_relay_credentials", username)
        self.relay = {"username": username, "password": password}


# ── Stalwart ────────────────────────────────────────────────────────────────

DAV, CALDAV, CARDDAV, APPLE = "DAV:", "urn:ietf:params:xml:ns:caldav", "urn:ietf:params:xml:ns:carddav", "http://apple.com/ns/ical/"
JMAP_USING = ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail", "urn:ietf:params:jmap:submission"]


def _x(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class StalwartBackend:
    name = "stalwart"
    transport: httpx.AsyncBaseTransport | None = None   # tests may set an httpx.MockTransport

    def __init__(self):
        s = config.settings
        if not s.stalwart_url or not s.stalwart_api_key:
            raise NotConfigured("STALWART_URL og STALWART_API_KEY skal være sat")
        self.base = s.stalwart_url.rstrip("/")

    def _client(self, auth: httpx.Auth | None = None, headers: dict | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, timeout=config.settings.stalwart_timeout, auth=auth,
                                 headers=headers, transport=self.transport)

    def _admin(self) -> httpx.AsyncClient:
        """STALWART_API_KEY is "<principal>:<secret>" (an admin principal or API key, HTTP Basic) or a bearer token."""
        key = config.settings.stalwart_api_key or ""
        if ":" in key:
            user, secret = key.split(":", 1)
            return self._client(auth=httpx.BasicAuth(user, secret))
        return self._client(headers={"Authorization": f"Bearer {key}"})

    def _as(self, account: str) -> httpx.AsyncClient:
        s = config.settings
        if not s.stalwart_master_user or not s.stalwart_master_secret:
            raise NotConfigured("STALWART_MASTER_USER og STALWART_MASTER_SECRET skal være sat")
        return self._client(auth=httpx.BasicAuth(f"{account}%{s.stalwart_master_user}", s.stalwart_master_secret))

    @staticmethod
    def _check(r: httpx.Response, what: str, ok: tuple[int, ...] = ()) -> None:
        if r.status_code >= 300 and r.status_code not in ok:
            raise MailBackendError(f"Stalwart {what}: HTTP {r.status_code} {r.text[:200]}")

    async def _api(self, method: str, path: str, body=None, *, ok: tuple[int, ...] = ()) -> dict | None:
        try:
            async with self._admin() as c:
                r = await c.request(method, path, json=body)
        except httpx.HTTPError as e:
            raise MailBackendError(f"Stalwart svarer ikke ({type(e).__name__})") from e
        if r.status_code == 404 and 404 in ok:
            return None
        self._check(r, f"{method} {path}", ok)
        data = r.json() if r.content else {}
        if isinstance(data, dict) and data.get("error"):
            if data["error"] in ("notFound", "not_found") and 404 in ok:
                return None
            raise MailBackendError(f"Stalwart {method} {path}: {data.get('error')} {data.get('details') or ''}".strip())
        return data

    # Principals
    async def principal(self, name: str) -> dict | None:
        data = await self._api("GET", f"/api/principal/{name}", ok=(404,))
        if not data:
            return None
        p = data.get("data") or {}
        return {"name": name, "type": p.get("type"), "emails": p.get("emails") or [],
                "quota_mb": (p.get("quota") or 0) // 1_048_576 or None, "used_bytes": p.get("usedQuota"),
                "enabled": "authenticate" not in (p.get("disabledPermissions") or []),
                "members": p.get("members") or [], "member_of": p.get("memberOf") or []}

    async def _patch(self, name: str, ops: list[dict]) -> None:
        if ops:
            await self._api("PATCH", f"/api/principal/{name}", ops)

    async def ensure_account(self, name: str, *, emails: list[str], quota_mb: int, description: str) -> None:
        if await self.principal(name) is None:
            await self._api("POST", "/api/principal", {"type": "individual", "name": name, "description": description,
                                                        "emails": emails, "quota": quota_mb * 1_048_576,
                                                        "roles": ["user"], "secrets": []})
        else:
            await self.update_principal(name, quota_mb=quota_mb, emails=emails, description=description)

    async def ensure_group(self, name: str, *, emails: list[str], description: str) -> None:
        if await self.principal(name) is None:
            await self._api("POST", "/api/principal", {"type": "group", "name": name, "description": description,
                                                        "emails": emails})
        else:
            await self.update_principal(name, emails=emails, description=description)

    async def update_principal(self, name: str, *, quota_mb: int | None = None, enabled: bool | None = None,
                               emails: list[str] | None = None, description: str | None = None) -> None:
        ops = []
        if quota_mb is not None:
            ops.append({"action": "set", "field": "quota", "value": quota_mb * 1_048_576})
        if emails is not None:
            ops.append({"action": "set", "field": "emails", "value": emails})
        if description is not None:
            ops.append({"action": "set", "field": "description", "value": description})
        if enabled is not None:
            # Suspension: no login and no inbound delivery; the mail stays until retention removes it.
            for perm in ("authenticate", "email-receive"):
                ops.append({"action": "removeItem" if enabled else "addItem", "field": "disabledPermissions",
                            "value": perm})
        await self._patch(name, ops)

    async def delete_principal(self, name: str) -> None:
        await self._api("DELETE", f"/api/principal/{name}", ok=(404,))

    async def list_principals(self) -> list[str]:
        names, page = [], 1
        while True:
            data = await self._api("GET", f"/api/principal?types=individual,group&limit=500&page={page}") or {}
            items = (data.get("data") or {}).get("items") or []
            names += [i["name"] if isinstance(i, dict) else str(i) for i in items]
            if len(items) < 500:
                return sorted(names)
            page += 1

    async def set_group_members(self, group: str, members: dict[str, str]) -> None:
        current = await self.principal(group)
        if current is None:
            raise MailBackendError(f"gruppen {group} findes ikke i Stalwart")
        senders = {m for m, role in members.items() if role in ("sender", "manager")}
        have = set(current["members"])
        for m in sorted(senders - have):
            await self._patch(m, [{"action": "addItem", "field": "memberOf", "value": group}])
        for m in sorted(have - senders):
            await self._patch(m, [{"action": "removeItem", "field": "memberOf", "value": group}])
        # Readers (and everyone else) get the group's mailboxes shared over JMAP; senders already see them as members.
        await self._share_mailboxes(group, {m: role for m, role in members.items()})

    async def _share_mailboxes(self, group: str, members: dict[str, str]) -> None:
        session, account = await self._jmap_session(group)
        boxes = (await self._jmap(group, session, [["Mailbox/get", {"accountId": account, "properties": ["id"]}, "0"]]))[0][1]
        share = {m: {"mayReadItems": True, "mayAddItems": True, "mayRemoveItems": role != "reader",
                     "maySetSeen": True, "maySetKeywords": True, "mayCreateChild": role == "manager",
                     "mayRename": role == "manager", "mayDelete": role == "manager", "maySubmit": role != "reader"}
                 for m, role in members.items()}
        update = {b["id"]: {"shareWith": share} for b in boxes.get("list", [])}
        if update:
            await self._jmap(group, session, [["Mailbox/set", {"accountId": account, "update": update}, "0"]])

    # Credentials
    async def add_app_password(self, account: str, secret_name: str, password: str) -> None:
        await self.remove_app_password(account, secret_name)
        await self._patch(account, [{"action": "addItem", "field": "secrets", "value": f"$app${secret_name}${password}"}])

    async def remove_app_password(self, account: str, secret_name: str) -> None:
        data = await self._api("GET", f"/api/principal/{account}", ok=(404,))
        if not data:
            return
        for value in (data.get("data") or {}).get("secrets") or []:
            if isinstance(value, str) and value.startswith(f"$app${secret_name}$"):
                await self._patch(account, [{"action": "removeItem", "field": "secrets", "value": value}])

    # JMAP (as an account)
    async def _jmap_session(self, account: str) -> tuple[dict, str]:
        async with self._as(account) as c:
            r = await c.get("/.well-known/jmap", follow_redirects=True)
        self._check(r, "JMAP session")
        session = r.json()
        acc = (session.get("primaryAccounts") or {}).get("urn:ietf:params:jmap:mail")
        if not acc:
            raise MailBackendError(f"JMAP: ingen mailkonto for {account}")
        return session, acc

    async def _jmap(self, account: str, session: dict, calls: list) -> list:
        api_url = session.get("apiUrl") or "/jmap/"
        async with self._as(account) as c:
            r = await c.post(api_url, json={"using": JMAP_USING, "methodCalls": calls})
        self._check(r, "JMAP")
        responses = r.json().get("methodResponses") or []
        for name, body, _ in responses:
            if name == "error":
                raise MailBackendError(f"JMAP-fejl: {body.get('type')} {body.get('description') or ''}".strip())
        return responses

    async def set_identity(self, account: str, address: str, display_name: str, signature: str) -> None:
        session, acc = await self._jmap_session(account)
        got = (await self._jmap(account, session, [["Identity/get", {"accountId": acc}, "0"]]))[0][1]
        existing = next((i for i in got.get("list", []) if (i.get("email") or "").lower() == address), None)
        props = {"name": display_name, "email": address, "textSignature": signature}
        call = (["Identity/set", {"accountId": acc, "update": {existing["id"]: {"name": display_name,
                                                                                 "textSignature": signature}}}, "0"]
                if existing else ["Identity/set", {"accountId": acc, "create": {"i": props}}, "0"])
        await self._jmap(account, session, [call])

    async def _mailbox_ids(self, account: str, session: dict, acc: str, role: str) -> list[str]:
        got = (await self._jmap(account, session, [["Mailbox/get", {"accountId": acc,
                                                                    "properties": ["id", "role"]}, "0"]]))[0][1]
        return [b["id"] for b in got.get("list", []) if b.get("role") == role]

    async def messages_before(self, account: str, before: datetime, applies_to: str) -> list[str]:
        session, acc = await self._jmap_session(account)
        filters: list[dict] = [{"before": before.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}]
        if applies_to != "all":
            boxes = await self._mailbox_ids(account, session, acc, applies_to)
            if not boxes:
                return []
            filters = [{**filters[0], "inMailbox": b} for b in boxes]
        ids: list[str] = []
        for f in filters:
            position = 0
            while True:
                got = (await self._jmap(account, session, [["Email/query", {
                    "accountId": acc, "filter": f, "position": position, "limit": 500}, "0"]]))[0][1]
                batch = got.get("ids") or []
                ids += batch
                if len(batch) < 500:
                    break
                position += len(batch)
        return list(dict.fromkeys(ids))

    async def raw_messages(self, account: str, ids: list[str]) -> list[tuple[str, bytes]]:
        session, acc = await self._jmap_session(account)
        got = (await self._jmap(account, session, [["Email/get", {"accountId": acc, "ids": ids,
                                                                  "properties": ["id", "blobId"]}, "0"]]))[0][1]
        template = session.get("downloadUrl") or "/jmap/download/{accountId}/{blobId}/{name}?accept={type}"
        out = []
        async with self._as(account) as c:
            for e in got.get("list", []):
                url = (template.replace("{accountId}", acc).replace("{blobId}", e["blobId"])
                       .replace("{name}", f"{e['id']}.eml").replace("{type}", "message/rfc822"))
                r = await c.get(url)
                self._check(r, "JMAP download")
                out.append((e["id"], r.content))
        return out

    async def destroy_messages(self, account: str, ids: list[str]) -> int:
        session, acc = await self._jmap_session(account)
        done = 0
        for i in range(0, len(ids), 200):
            got = (await self._jmap(account, session, [["Email/set", {"accountId": acc,
                                                                      "destroy": ids[i:i + 200]}, "0"]]))[0][1]
            done += len(got.get("destroyed") or [])
        return done

    # WebDAV (calendars and address books, as the owning account)
    def _href(self, owner: str, type: str, slug: str = "") -> str:
        return f"/dav/{DAV_ROOT[type]}/{owner}/" + (f"{slug}/" if slug else "")

    async def _dav(self, owner: str, method: str, path: str, body: str | bytes | None = None, *,
                   depth: str | None = None, content_type: str = "application/xml; charset=utf-8",
                   ok: tuple[int, ...] = ()) -> httpx.Response:
        headers = {"Content-Type": content_type}
        if depth is not None:
            headers["Depth"] = depth
        try:
            async with self._as(owner) as c:
                r = await c.request(method, path, content=body, headers=headers)
        except httpx.HTTPError as e:
            raise MailBackendError(f"Stalwart svarer ikke ({type(e).__name__})") from e
        self._check(r, f"{method} {path}", ok)
        return r

    async def ensure_collection(self, owner: str, type: str, slug: str, *, name: str, colour: str | None = None,
                                timezone: str | None = None) -> None:
        path = self._href(owner, type, slug)
        exists = (await self._dav(owner, "PROPFIND", path, "<D:propfind xmlns:D='DAV:'><D:prop><D:resourcetype/>"
                                  "</D:prop></D:propfind>", depth="0", ok=(404,))).status_code != 404
        props = f"<D:displayname>{_x(name)}</D:displayname>"
        if type == CALENDAR and colour:
            props += f"<A:calendar-color>{_x(colour)}</A:calendar-color>"
        if type == CALENDAR and timezone:
            props += f"<C:calendar-timezone-id>{_x(timezone)}</C:calendar-timezone-id>"
        ns = f"xmlns:D='{DAV}' xmlns:C='{CALDAV}' xmlns:A='{APPLE}' xmlns:R='{CARDDAV}'"
        if not exists and type == CALENDAR:
            await self._dav(owner, "MKCALENDAR", path,
                            f"<C:mkcalendar {ns}><D:set><D:prop>{props}</D:prop></D:set></C:mkcalendar>")
        elif not exists:
            await self._dav(owner, "MKCOL", path,
                            f"<D:mkcol {ns}><D:set><D:prop><D:resourcetype><D:collection/><R:addressbook/>"
                            f"</D:resourcetype>{props}</D:prop></D:set></D:mkcol>")
        else:
            await self._dav(owner, "PROPPATCH", path,
                            f"<D:propertyupdate {ns}><D:set><D:prop>{props}</D:prop></D:set></D:propertyupdate>")

    async def delete_collection(self, owner: str, type: str, slug: str) -> None:
        await self._dav(owner, "DELETE", self._href(owner, type, slug), ok=(404,))

    async def set_collection_access(self, owner: str, type: str, slug: str, grants: dict[str, str]) -> None:
        aces = "".join(
            f"<D:ace><D:principal><D:href>/dav/pal/{_x(p)}/</D:href></D:principal><D:grant>"
            + "".join(f"<D:privilege><D:{priv}/></D:privilege>" for priv in COLLECTION_PRIVILEGES[role])
            + "</D:grant></D:ace>"
            for p, role in sorted(grants.items()))
        await self._dav(owner, "ACL", self._href(owner, type, slug), f"<D:acl xmlns:D='{DAV}'>{aces}</D:acl>")

    async def set_freebusy(self, owner: str, slug: str, opaque: bool) -> None:
        value = "<C:opaque/>" if opaque else "<C:transparent/>"
        await self._dav(owner, "PROPPATCH", self._href(owner, CALENDAR, slug),
                        f"<D:propertyupdate xmlns:D='{DAV}' xmlns:C='{CALDAV}'><D:set><D:prop>"
                        f"<C:schedule-calendar-transp>{value}</C:schedule-calendar-transp></D:prop></D:set>"
                        "</D:propertyupdate>")

    async def collection_items(self, owner: str, type: str, slug: str) -> dict[str, str]:
        if type == CALENDAR:
            body = (f"<C:calendar-query xmlns:D='{DAV}' xmlns:C='{CALDAV}'><D:prop><C:calendar-data/></D:prop>"
                    "<C:filter><C:comp-filter name='VCALENDAR'/></C:filter></C:calendar-query>")
            data_tag = f"{{{CALDAV}}}calendar-data"
        else:
            body = (f"<R:addressbook-query xmlns:D='{DAV}' xmlns:R='{CARDDAV}'><D:prop><R:address-data/></D:prop>"
                    "</R:addressbook-query>")
            data_tag = f"{{{CARDDAV}}}address-data"
        r = await self._dav(owner, "REPORT", self._href(owner, type, slug), body, depth="1")
        items = {}
        for resp in ET.fromstring(r.content).iter(f"{{{DAV}}}response"):
            href = resp.findtext(f"{{{DAV}}}href") or ""
            data = next((el.text for el in resp.iter(data_tag) if el.text), None)
            if data:
                items[href.rstrip("/").rsplit("/", 1)[-1]] = data
        return items

    async def put_items(self, owner: str, type: str, slug: str, items: dict[str, str]) -> int:
        ctype = "text/calendar; charset=utf-8" if type == CALENDAR else "text/vcard; charset=utf-8"
        for name, data in items.items():
            await self._dav(owner, "PUT", self._href(owner, type, slug) + name, data.encode(), content_type=ctype)
        return len(items)

    async def set_availability(self, account: str, *, publish: bool, working_hours: dict, tz: str) -> None:
        days = ",".join(["MO", "TU", "WE", "TH", "FR", "SA", "SU"][d - 1] for d in working_hours.get("days", []))
        start, end = working_hours.get("start", "08:00").replace(":", ""), working_hours.get("end", "16:00").replace(":", "")
        vav = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Gaer//Mail//DA\r\nBEGIN:VAVAILABILITY\r\n"
               f"UID:gaer-{account}-hours\r\nDTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}\r\n"
               "BEGIN:AVAILABLE\r\n"
               f"UID:gaer-{account}-hours-1\r\nDTSTART;TZID={tz}:20240101T{start}00\r\n"
               f"DTEND;TZID={tz}:20240101T{end}00\r\nRRULE:FREQ=WEEKLY;BYDAY={days or 'MO'}\r\n"
               "END:AVAILABLE\r\nEND:VAVAILABILITY\r\nEND:VCALENDAR\r\n")
        home = f"/dav/cal/{account}/"
        await self._dav(account, "PROPPATCH", home,
                        f"<D:propertyupdate xmlns:D='{DAV}' xmlns:C='{CALDAV}'><D:set><D:prop>"
                        f"<C:calendar-availability>{_x(vav)}</C:calendar-availability></D:prop></D:set>"
                        "</D:propertyupdate>")
        grant = ("<D:ace><D:principal><D:authenticated/></D:principal><D:grant><D:privilege>"
                 "<C:read-free-busy/></D:privilege></D:grant></D:ace>") if publish else ""
        await self._dav(account, "ACL", home, f"<D:acl xmlns:D='{DAV}' xmlns:C='{CALDAV}'>{grant}</D:acl>")

    # Operations
    async def health(self) -> dict:
        out: dict = {"reachable": False, "version": None, "uptime_seconds": None, "queue": None, "stores": None}
        try:
            async with self._admin() as c:
                r = await c.get("/healthz/ready")
            out["reachable"] = r.status_code < 300
            out["version"] = r.headers.get("server")
            out["stores"] = {"data": r.status_code < 300, "blob": r.status_code < 300}
            q = await self._api("GET", "/api/queue/messages?limit=1&page=1") or {}
            data = q.get("data") or {}
            items = data.get("items") or []
            oldest = items[0].get("created") if items and isinstance(items[0], dict) else None
            out["queue"] = {"messages": data.get("total", len(items)), "oldest_at": oldest, "paused": None}
        except (httpx.HTTPError, MailBackendError) as e:
            out["error"] = str(e)
        return out

    async def queue_action(self, action: str) -> None:
        if action == "pause":
            await self._api("PATCH", "/api/queue/status/stop")
        elif action == "resume":
            await self._api("PATCH", "/api/queue/status/start")
        elif action == "flush":
            await self._api("PATCH", "/api/queue/messages")

    async def set_relay_credentials(self, username: str, password: str) -> None:
        await self._api("POST", "/api/settings", [{"type": "insert", "prefix": "queue.route.lettermint.auth",
                                                   "values": [["username", username], ["secret", password]],
                                                   "assert_empty": False}])
        await self._api("GET", "/api/reload")


# ── Choosing one ────────────────────────────────────────────────────────────

_memory = MemoryBackend()


def enabled() -> bool:
    return config.settings.mailbox_backend in ("memory", "stalwart")


def get_backend():
    kind = config.settings.mailbox_backend
    if kind == "memory":
        if config.settings.is_production:
            raise NotConfigured("MAILBOX_BACKEND=memory er ikke tilladt i produktion")
        return _memory
    if kind == "stalwart":
        return StalwartBackend()
    raise NotConfigured("Mail er ikke sat op (MAILBOX_BACKEND)")


def memory() -> MemoryBackend:
    return _memory


def new_app_password() -> str:
    """24 characters from an alphabet without look-alikes, in groups of four for typing on a phone."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(24))
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))
