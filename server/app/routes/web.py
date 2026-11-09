"""/w — the public website, server-rendered from Markdown pages edited in /admin/web.

Language: ?lang=da|en (remembered in the w_lang cookie), then the cookie, then Danish.
Front page: the Markdown page with slug '' (default), or — with the setting web_front = "facit246" (/admin/web) —
the fixed "Facit · 246" story (app/web_front.py) in da/en/de/sv/no, where the browser's language is used when
nothing is chosen. Both end with the login card.
Anonymous visitors to / are sent here (see main.py). Page views go through the same
self-hosted analytics as the app (static/w.js), with the same consent.
"""

import io
import os
import zipfile
from html import escape
from html import unescape as html_unescape

import markdown
import nh3
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app import config, db, og, settings_store, web_front
from app.routes.auth import admin_open_to_anyone

router = APIRouter(tags=["web"])

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web_static")
LANGS = ("da", "en")
TEXT = {
    "da": {"login": "Log ind", "privacy": "Privatliv", "other": "English", "missing": "Siden findes ikke",
           "card_title": "Log ind", "card_text": "Brug din e-mailadresse. Du får en engangskode på mail.",
           "first": "Instansen er ny og har ingen brugere endnu. Den første, der logger ind, bliver administrator."},
    "en": {"login": "Log in", "privacy": "Privacy", "other": "Dansk", "missing": "Page not found",
           "card_title": "Log in", "card_text": "Use your e-mail address. We'll e-mail you a one-time code.",
           "first": "This instance is new and has no users yet. The first person to log in becomes the administrator."},
}
PLACEHOLDER = {
    "da": "# Velkommen\n\nHer kommer jeres website. Indholdet redigeres i /admin/web.",
    "en": "# Welcome\n\nYour website goes here. Edit the content in /admin/web.",
}


def _login_card(lang: str, first_user: bool) -> str:
    t = TEXT[lang]
    notice = f'<p class="first">{t["first"]}</p>' if first_user else ""
    return f"""<section class="login-card">
  <h2>{t['card_title']}</h2>
  <p>{t['card_text']}</p>
  {notice}
  <a class="button" href="/login">{t['login']}</a>
</section>"""
ALLOWED_TAGS = {"h1", "h2", "h3", "h4", "p", "a", "ul", "ol", "li", "strong", "em", "code", "pre", "blockquote",
                "img", "hr", "br", "table", "thead", "tbody", "tr", "th", "td"}


def render_markdown(source: str) -> str:
    html = markdown.markdown(source, extensions=["tables", "fenced_code", "sane_lists"])
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes={"a": {"href", "title"}, "img": {"src", "alt", "title"}},
                     url_schemes={"http", "https", "mailto", "tel"})


def _browser_lang(request: Request, allowed: tuple[str, ...]) -> str | None:
    for part in request.headers.get("accept-language", "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        code = "no" if code in ("nb", "nn") else code
        if code in allowed:
            return code
    return None


def _lang(request: Request, allowed: tuple[str, ...] = LANGS, browser: bool = False) -> tuple[str, bool]:
    q = request.query_params.get("lang")
    if q in allowed:
        return q, True
    c = request.cookies.get("w_lang")
    if c in allowed:
        return c, False
    if browser and not c:
        return _browser_lang(request, allowed) or "da", False
    return "da", False


def _page_html(*, lang: str, page: dict | None, nav: list[dict], values: dict, slug: str,
               first_user: bool = False) -> str:
    t = TEXT[lang]
    other = "en" if lang == "da" else "da"
    name = escape(values["app_name"])
    title = escape(page["title"]) if page else t["missing"]
    description = escape(page["description"]) if page else ""
    base = config.settings.origin.rstrip("/")
    path = f"/w/{slug}" if slug else "/w/"
    logo = '<img src="/api/public/logo" alt="">' if values["logo_key"] else '<img src="/favicon.svg" alt="">'
    links = "".join(
        f'<a href="/w/{escape(p["slug"])}"{" class=active" if p["slug"] == slug else ""}>{escape(p["title"])}</a>'
        for p in nav if p["slug"])
    if page:
        body = render_markdown(page["body"])
    elif slug == "":
        body = render_markdown(PLACEHOLDER[lang])  # no front page written yet
        title = escape(values["app_name"])
    else:
        body = f"<h1>{t['missing']}</h1>"
    if slug == "":
        body += _login_card(lang, first_user)
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {name}</title>
<meta name="description" content="{description}">
{og.meta_tags(title=html_unescape(title), description=html_unescape(description), site_name=values["app_name"],
              image=og.image_url("w" if slug == "" else "default", values["og_images"], page), url=f"{base}{path}")}
<link rel="canonical" href="{base}{path}?lang={lang}">
<link rel="alternate" hreflang="da" href="{base}{path}?lang=da"><link rel="alternate" hreflang="en" href="{base}{path}?lang=en">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/w/static/w.css">
<script src="/w/static/w.js" defer></script>
</head>
<body>
<header class="top"><div class="inner">
  <a class="brand" href="/w/">{logo}{name}</a>
  <nav>{links}<a href="{path}?lang={other}" class="lang">{t['other']}</a><a class="login" href="/login">{t['login']}</a></nav>
</div></header>
<main class="content">{body}</main>
<footer><div class="inner"><span>© {name}</span><a href="/privatliv">{t['privacy']}</a></div></footer>
</body>
</html>"""


@router.get("/w", include_in_schema=False)
async def w_root():
    return RedirectResponse("/w/", status_code=308)


@router.get("/w/static/{name}", include_in_schema=False)
async def w_static(name: str):
    if name not in ("w.css", "w.js", "facit.css", "facit.js"):
        raise HTTPException(404)
    return FileResponse(os.path.join(STATIC_DIR, name), headers={"Cache-Control": "public, max-age=300"})


@router.get("/w/{slug:path}", response_class=HTMLResponse, include_in_schema=False)
async def w_page(slug: str, request: Request):
    slug = slug.strip("/")
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        story = slug == "" and values["web_front"] == "facit246"
        lang, explicit = _lang(request, web_front.LANGS, browser=True) if story else _lang(request)
        page_lang = lang if lang in LANGS else "da"  # Markdown pages and the menu exist in da/en
        page = None if story else await db.fetch_one(
            conn, "select * from web_pages where slug = %s and lang = %s and published", (slug, lang))
        nav = await db.fetch_all(conn, """select slug, title from web_pages where lang = %s and published
                                          and nav_order is not null order by nav_order, title""", (page_lang,))
        first_user = await admin_open_to_anyone(conn)
    if story:
        html = web_front.render(lang=lang, app_name=values["app_name"], base=config.settings.origin.rstrip("/"),
                                nav=nav, first_user=first_user, og_image=og.image_url("w", values["og_images"]))
    else:
        html = _page_html(lang=lang, page=page, nav=nav, values=values, slug=slug, first_user=first_user)
    found = page is not None or slug == ""
    response = HTMLResponse(html, status_code=200 if found else 404, headers={"Cache-Control": "no-cache"})
    if explicit:
        response.set_cookie("w_lang", lang, max_age=365 * 86400, samesite="lax", secure=config.settings.secure_cookies)
    return response


# ── Browser extension download ─────────────────────────────────────────────

@router.get("/api/extension/download", include_in_schema=False)
async def extension_download():
    """The extension in extension/, zipped with this instance's address as the default endpoint.
    Load it in Chrome/Edge via chrome://extensions → Load unpacked, or publish it to the stores."""
    root = os.path.abspath(config.settings.extension_dir)
    if not os.path.isdir(root):
        raise HTTPException(404, "Udvidelsen er ikke med i dette build")
    async with db.connection() as conn:
        name = await settings_store.get(conn, "app_name")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _, files in os.walk(root):
            for f in files:
                full = os.path.join(folder, f)
                rel = os.path.relpath(full, root)
                data = open(full, "rb").read()
                if rel in ("manifest.json", "config.js"):
                    data = (data.decode().replace("__GAER_ENDPOINT__", config.settings.origin.rstrip("/"))
                            .replace("__GAER_NAME__", name.replace('"', ""))).encode()
                z.writestr(os.path.join("gaer-extension", rel), data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="gaer-extension.zip"'})
