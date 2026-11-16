"""AI consumption: which limit applies to a user, budgets with warnings, and the numbers behind /admin/consumption
and the user's own page (/konto/forbrug).

What applies to a user today, in this order:
1. an active budget (ai_budgets: starts_on ≤ today ≤ ends_on, or no end date) — monthly cap, warning levels,
   hard or soft, and optionally a total for the whole period;
2. the user's own monthly limit (users.ai_monthly_tokens, set in /admin/ai);
3. the instance default (settings: ai_default_monthly_tokens, ai_default_warn_pct).

A hard cap stops AI for the rest of the month (or for the rest of the period, for a used-up total); a soft cap
only warns. Each level a user passes is stored once per month in ai_budget_alerts, sent as a webhook and, when
the setting ai_warning_mail is on and the user has an e-mail address, mailed to the user (job ai.budget.mail).
Only token counts are used; prompts and answers are never stored (see app/ai.py).
"""

from datetime import date, datetime, timedelta

from app import config, db, jobs, mail, outbound_mail, settings_store, webhooks

WARN_CHOICES = (10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90, 95)
RANGES = ("day", "week", "month", "year", "custom")
MAX_CUSTOM_DAYS = 731

ACTIVE_BUDGET = """
    select id, user_id, monthly_tokens, warn_pcts, hard, total_tokens, starts_on, ends_on, note
    from ai_budgets
    where starts_on <= current_date and (ends_on is null or ends_on >= current_date)"""


def budget_status(b: dict, today: date) -> str:
    if b["starts_on"] > today:
        return "planned"
    if b["ends_on"] is not None and b["ends_on"] < today:
        return "expired"
    return "active"


# ── What applies to a user ─────────────────────────────────────────────────

async def _defaults(conn) -> tuple[int, list[int]]:
    s = await settings_store.get_all(conn)
    warn = int(s["ai_default_warn_pct"] or 0)
    return int(s["ai_default_monthly_tokens"]), [warn] if warn else []


async def policies(conn, user_ids: list[int]) -> dict[int, dict]:
    """The policy for each user today, plus this month's use and the period total of a budget. One query per kind."""
    if not user_ids:
        return {}
    default_limit, default_warn = await _defaults(conn)
    budgets = {b["user_id"]: b for b in await db.fetch_all(conn, ACTIVE_BUDGET + """
        and user_id = any(%s) order by user_id, starts_on, id""", (user_ids,))}
    own = {r["id"]: r["ai_monthly_tokens"] for r in await db.fetch_all(
        conn, "select id, ai_monthly_tokens from users where id = any(%s)", (user_ids,))}
    month = {r["user_id"]: int(r["n"]) for r in await db.fetch_all(conn, """
        select user_id, sum(prompt_tokens + completion_tokens) as n from ai_usage
        where user_id = any(%s) and ts >= date_trunc('month', now()) group by user_id""", (user_ids,))}
    totals: dict[int, int] = {}
    for b in budgets.values():
        if b["total_tokens"] is not None:
            row = await db.fetch_one(conn, """
                select coalesce(sum(prompt_tokens + completion_tokens), 0) as n from ai_usage
                where user_id = %s and ts >= %s::date and (%s::date is null or ts < %s::date + 1)""",
                (b["user_id"], b["starts_on"], b["ends_on"], b["ends_on"]))
            totals[b["user_id"]] = int(row["n"])

    out = {}
    for uid in user_ids:
        b = budgets.get(uid)
        if b:
            p = {"source": "budget", "limit": b["monthly_tokens"], "warn_pcts": sorted(b["warn_pcts"]), "hard": b["hard"],
                 "budget": {"id": b["id"], "starts_on": b["starts_on"], "ends_on": b["ends_on"],
                            "total_tokens": b["total_tokens"], "total_used": totals.get(uid)}}
        elif own.get(uid) is not None:
            p = {"source": "user", "limit": own[uid], "warn_pcts": default_warn, "hard": True, "budget": None}
        else:
            p = {"source": "default", "limit": default_limit, "warn_pcts": default_warn, "hard": True, "budget": None}
        out[uid] = _evaluate(p, month.get(uid, 0))
    return out


def _evaluate(p: dict, used: int) -> dict:
    limit = p["limit"]
    pct = round(used * 100 / limit, 1) if limit > 0 else None
    passed = [w for w in p["warn_pcts"] if limit > 0 and used * 100 >= w * limit]
    over_cap = limit > 0 and used >= limit
    b = p["budget"]
    total_spent = bool(b and b["total_tokens"] and (b["total_used"] or 0) >= b["total_tokens"])
    blocked = None
    if limit <= 0:
        blocked = "off"
    elif p["hard"] and over_cap:
        blocked = "cap"
    elif p["hard"] and total_spent:
        blocked = "total"
    if blocked == "off":
        status = "off"
    elif blocked:
        status = "blocked"
    elif over_cap or total_spent:
        status = "over"
    elif passed:
        status = "warning"
    else:
        status = "ok"
    return {**p, "used": used, "pct": pct, "warning": max(passed) if passed else None, "over_cap": over_cap,
            "total_spent": total_spent, "blocked": blocked, "status": status}


async def policy(conn, user_id: int) -> dict:
    return (await policies(conn, [user_id]))[user_id]


BLOCKED_TEXT = {
    "off": "AI er ikke slået til for din bruger",
    "cap": "Du har brugt din AI-kvote for denne måned",
    "total": "Du har brugt hele dit AI-budget for perioden",
}

AI_OFF_TEXT = "AI er ikke slået til på denne instans"
AI_CLOSED_TEXT = "AI er lukket lige nu"


async def check(conn, user_id: int | None) -> tuple[int, str] | None:
    """The one check before any AI call (specs/developer/AI.md, AI-3): AI configured, the kill switch "ai" open, and
    — for a user — their limit and budget. None when the call may go ahead, else (HTTP status, Danish text).
    Calls without a user (jobs) only check the first two."""
    from app import ai, killswitch
    if not ai.enabled():
        return 404, AI_OFF_TEXT
    if await killswitch.is_active("ai"):
        return 503, AI_CLOSED_TEXT
    if user_id is None:
        return None
    pol = await policy(conn, user_id)
    if pol["blocked"] == "off":
        return 403, BLOCKED_TEXT["off"]
    if pol["blocked"]:
        return 429, BLOCKED_TEXT[pol["blocked"]]
    return None


# ── After each AI call ─────────────────────────────────────────────────────

async def after_usage(conn, user_id: int) -> list[dict]:
    """Stores the levels the user has now passed (once per month); for each new one a webhook and a mail job."""
    p = await policy(conn, user_id)
    if p["limit"] <= 0:
        return []
    budget_id = p["budget"]["id"] if p["budget"] else None
    levels = [("warning", w, p["used"], p["limit"]) for w in p["warn_pcts"] if p["used"] * 100 >= w * p["limit"]]
    if p["over_cap"]:
        levels.append(("cap", 100, p["used"], p["limit"]))
    if p["total_spent"]:
        levels.append(("total", 100, p["budget"]["total_used"], p["budget"]["total_tokens"]))
    new = []
    for kind, pct, used, limit in levels:
        row = await db.fetch_one(conn, """
            insert into ai_budget_alerts (user_id, budget_id, month, kind, pct, used, limit_tokens)
            values (%s, %s, date_trunc('month', now())::date, %s, %s, %s, %s)
            on conflict (user_id, month, kind, pct) do nothing returning id""",
            (user_id, budget_id, kind, pct, used, limit))
        if row:
            new.append({"id": row["id"], "kind": kind, "pct": pct})
            await webhooks.emit(conn, f"ai.budget.{kind}", {"user_id": user_id, "budget_id": budget_id, "alert_id": row["id"],
                                                             "pct": pct, "hard": p["hard"]})
    if new and await settings_store.get(conn, "ai_warning_mail"):
        # One mail for the highest new level: passing 80 % and the cap in one call is one mail about the cap.
        top = max(new, key=lambda a: (a["kind"] != "warning", a["kind"] == "total", a["pct"]))
        await jobs.enqueue(conn, "ai.budget.mail", {"alert_id": top["id"], "hard": p["hard"]},
                           dedupe_key=f"ai.budget.mail:{top['id']}")
    return new


MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november",
          "december"]


@jobs.handler("ai.budget.mail")
async def send_alert_mail(payload: dict, job: dict) -> dict:
    """Mails the user about a budget alert. Skipped when the user has no e-mail, the alert is gone (erased or
    purged) or it was already mailed. A mail error raises, so the job is retried."""
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """
            select a.id, a.kind, a.pct, a.used, a.limit_tokens, a.mailed_at, a.month, u.email, a.user_id
            from ai_budget_alerts a join users u on u.id = a.user_id where a.id = %s""", (payload["alert_id"],))
        if row is None:
            return {"skipped": "alert not found"}
        if row["mailed_at"] is not None:
            return {"skipped": "already mailed"}
        if not row["email"]:
            return {"skipped": "no e-mail"}
        app_name = await settings_store.get(conn, "app_name")
    m = row["month"]
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    subject, text, body = mail.ai_budget_mail(row["kind"], row["pct"], row["used"], row["limit_tokens"],
                                              bool(payload.get("hard", True)), app_name,
                                              config.settings.origin.rstrip("/") + "/konto/forbrug",
                                              f"{nxt.day}. {MONTHS[nxt.month - 1]} {nxt.year}")
    message_id = await mail.send_mail(row["email"], subject, text, body, app_name)
    async with db.connection() as conn:
        await conn.execute("update ai_budget_alerts set mailed_at = now() where id = %s", (row["id"],))
        await outbound_mail.record_sent(conn, message_id=message_id, recipient=row["email"], user_id=row.get("user_id"),
                                        purpose="budget", subject=subject)
    return {"sent": row["kind"], "pct": row["pct"]}


# ── Periods ────────────────────────────────────────────────────────────────

def _add_months(d: datetime, n: int) -> datetime:
    m = d.month - 1 + n
    return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=1)


async def period(conn, range_: str, start: date | None = None, end: date | None = None) -> dict:
    """[start, end) in the database's time zone, the bucket size and the previous period of the same length."""
    now = await db.fetch_one(conn, "select date_trunc('day', now()) as today, date_trunc('month', now()) as month")
    today, month = now["today"], now["month"]
    if range_ == "day":
        lo, hi, bucket = today, today + timedelta(days=1), "hour"
    elif range_ == "week":
        lo, hi, bucket = today - timedelta(days=6), today + timedelta(days=1), "day"
    elif range_ == "month":
        lo, hi, bucket = today - timedelta(days=29), today + timedelta(days=1), "day"
    elif range_ == "year":
        lo, hi, bucket = _add_months(month, -11), _add_months(month, 1), "month"
    else:
        if start is None or end is None or end < start:
            raise ValueError("Vælg en start- og slutdato")
        if (end - start).days > MAX_CUSTOM_DAYS:
            raise ValueError("Perioden må højst være to år")
        lo = today.replace(year=start.year, month=start.month, day=start.day)
        hi = today.replace(year=end.year, month=end.month, day=end.day) + timedelta(days=1)
        bucket = "day" if (end - start).days <= 62 else "month"
        if bucket == "month":
            lo = lo.replace(day=1)
    if bucket == "month":
        span = (hi.year - lo.year) * 12 + hi.month - lo.month
        prev = (_add_months(lo, -span), lo)
    else:
        prev = (lo - (hi - lo), lo)
    return {"range": range_, "start": lo, "end": hi, "bucket": bucket, "prev_start": prev[0], "prev_end": prev[1]}


# ── Reports ────────────────────────────────────────────────────────────────

COST = """coalesce(sum(u.prompt_tokens * coalesce(p.input_per_million, 0)
                       + u.completion_tokens * coalesce(p.output_per_million, 0)) / 1000000, 0)"""


async def report(conn, per: dict) -> dict:
    span = (per["start"], per["end"])
    totals = await db.fetch_one(conn, f"""
        select count(*) as requests, coalesce(sum(u.prompt_tokens), 0) as prompt_tokens,
               coalesce(sum(u.completion_tokens), 0) as completion_tokens, count(distinct u.user_id) as users,
               count(*) filter (where u.estimated) as estimated, {COST} as cost
        from ai_usage u left join ai_model_prices p on p.model = u.model where u.ts >= %s and u.ts < %s""", span)
    prev = await db.fetch_one(conn, f"""
        select count(*) as requests, coalesce(sum(u.prompt_tokens + u.completion_tokens), 0) as tokens, {COST} as cost
        from ai_usage u left join ai_model_prices p on p.model = u.model where u.ts >= %s and u.ts < %s""",
        (per["prev_start"], per["prev_end"]))
    step = {"hour": "1 hour", "day": "1 day", "month": "1 month"}[per["bucket"]]
    buckets = await db.fetch_all(conn, f"""
        select g as start from generate_series(%s::timestamptz, %s::timestamptz - interval '{step}', interval '{step}') g""",
        span)
    series_rows = await db.fetch_all(conn, f"""
        select date_trunc('{per["bucket"]}', ts) as start, model, sum(prompt_tokens + completion_tokens) as tokens,
               count(*) as requests
        from ai_usage where ts >= %s and ts < %s group by 1, 2""", span)
    by_bucket: dict = {}
    for r in series_rows:
        by_bucket.setdefault(r["start"], {})[r["model"]] = int(r["tokens"])
    series = [{"start": b["start"], "models": by_bucket.get(b["start"], {}),
               "tokens": sum(by_bucket.get(b["start"], {}).values())} for b in buckets]
    models = await db.fetch_all(conn, f"""
        select u.model, count(*) as requests, sum(u.prompt_tokens) as prompt_tokens,
               sum(u.completion_tokens) as completion_tokens, count(distinct u.user_id) as users, max(u.ts) as last_used,
               {COST} as cost, (p.model is not null) as priced
        from ai_usage u left join ai_model_prices p on p.model = u.model
        where u.ts >= %s and u.ts < %s group by u.model, p.model order by sum(u.prompt_tokens + u.completion_tokens) desc""",
        span)
    surfaces = await db.fetch_all(conn, """
        select surface, count(*) as requests, sum(prompt_tokens + completion_tokens) as tokens
        from ai_usage where ts >= %s and ts < %s group by surface order by tokens desc""", span)
    heat = await db.fetch_all(conn, """
        select extract(isodow from ts)::int as dow, extract(hour from ts)::int as hour,
               sum(prompt_tokens + completion_tokens) as tokens
        from ai_usage where ts >= %s and ts < %s group by 1, 2""", span)
    users = await db.fetch_all(conn, f"""
        select u.user_id as id, us.display_name, us.email, us.phone, count(*) as requests,
               sum(u.prompt_tokens + u.completion_tokens) as tokens, {COST} as cost,
               mode() within group (order by u.model) as top_model, max(u.ts) as last_used
        from ai_usage u join users us on us.id = u.user_id left join ai_model_prices p on p.model = u.model
        where u.ts >= %s and u.ts < %s group by u.user_id, us.id order by tokens desc limit 500""", span)
    # Users with their own budget or limit but no use in the period still belong in the list.
    quiet = await db.fetch_all(conn, f"""
        select us.id, us.display_name, us.email, us.phone, 0 as requests, 0 as tokens, 0 as cost,
               null as top_model, null::timestamptz as last_used
        from users us
        where (us.ai_monthly_tokens is not null or exists ({ACTIVE_BUDGET} and user_id = us.id))
          and not us.impersonation_only and us.id <> all(%s)
        order by us.display_name nulls last, us.id limit 200""", ([u["id"] for u in users],))
    users = users + quiet
    pol = await policies(conn, [u["id"] for u in users])
    for u in users:
        u["policy"] = pol[u["id"]]
    alerts = await db.fetch_all(conn, """
        select kind, count(*) as n from ai_budget_alerts where month = date_trunc('month', now())::date group by kind""")
    month_status = {"warning": 0, "over": 0, "blocked": 0}
    for p in pol.values():
        if p["status"] in month_status:
            month_status[p["status"]] += 1
    return {"period": per, "totals": totals, "previous": prev, "series": series, "models": models,
            "surfaces": surfaces, "heat": heat, "users": users, "alerts": {r["kind"]: r["n"] for r in alerts},
            "month_status": month_status}


async def user_report(conn, user_id: int) -> dict:
    """The user's own page: this month, what applies, where it was used, the last 12 months."""
    p = await policy(conn, user_id)
    default_limit, _ = await _defaults(conn)
    own = await db.fetch_one(conn, "select ai_monthly_tokens from users where id = %s", (user_id,))
    after = own["ai_monthly_tokens"] if own and own["ai_monthly_tokens"] is not None else default_limit
    bounds = await db.fetch_one(conn, """
        select date_trunc('month', now()) as start, date_trunc('month', now()) + interval '1 month' as next""")
    days = await db.fetch_all(conn, """
        select g::date as day, coalesce(sum(u.prompt_tokens + u.completion_tokens), 0) as tokens
        from generate_series(%s::timestamptz, %s::timestamptz - interval '1 day', interval '1 day') g
        left join ai_usage u on u.user_id = %s and u.ts >= g and u.ts < g + interval '1 day'
        group by g order by g""", (bounds["start"], bounds["next"], user_id))
    surfaces = await db.fetch_all(conn, """
        select surface, count(*) as requests, sum(prompt_tokens + completion_tokens) as tokens
        from ai_usage where user_id = %s and ts >= date_trunc('month', now()) group by surface order by tokens desc""",
        (user_id,))
    models = await db.fetch_all(conn, """
        select model, count(*) as requests, sum(prompt_tokens + completion_tokens) as tokens
        from ai_usage where user_id = %s and ts >= date_trunc('month', now()) group by model order by tokens desc""",
        (user_id,))
    months = await db.fetch_all(conn, """
        select date_trunc('month', ts)::date as month, count(*) as requests, sum(prompt_tokens + completion_tokens) as tokens
        from ai_usage where user_id = %s and ts >= date_trunc('month', now()) - interval '11 months'
        group by 1 order by 1 desc""", (user_id,))
    alerts = await db.fetch_all(conn, """
        select kind, pct, used, limit_tokens, created_at from ai_budget_alerts
        where user_id = %s and month = date_trunc('month', now())::date order by created_at desc""", (user_id,))
    upcoming = await db.fetch_all(conn, """
        select monthly_tokens, warn_pcts, hard, total_tokens, starts_on, ends_on from ai_budgets
        where user_id = %s and starts_on > current_date order by starts_on limit 3""", (user_id,))
    return {"policy": p, "after_budget_limit": after, "month": {"start": bounds["start"], "resets_at": bounds["next"]},
            "days": days, "surfaces": surfaces, "models": models, "months": months, "alerts": alerts,
            "upcoming": upcoming}
