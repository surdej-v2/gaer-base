"""og:image and the other sharing tags for the pages anonymous visitors see: the website (/w), /login, /privatliv.

Every surface has a generated image in web_static/og/<name>.png (made from the brand: the Happy Mates mark and
the name). An admin can replace any of them in /admin/web — per surface (settings.og_images, a map name →
storage key) and per Markdown page (web_pages.og_image_key). Which image a page gets:

    the page's own upload → the surface's upload → the uploaded default → the surface's generated image → default.png

The images are served by /api/public/og/<name> (and /api/public/og/page-<id>); the meta tags carry absolute URLs
with ?v=<version>, so a new upload is picked up even where the old one was cached.
"""

import hashlib
import os
from html import escape

from app import config

STATIC = os.path.join(os.path.dirname(__file__), "web_static", "og")

# The surfaces an admin can give their own image, in the order /admin/web shows them.
SURFACES: dict[str, str] = {
    "default": "Standard — alle offentlige sider uden eget billede",
    "w": "Forsiden /w/",
    "login": "Log ind (/login)",
    "privatliv": "Privatliv (/privatliv)",
}

DESCRIPTIONS = {
    "login": "Log ind med en engangskode på mail eller med en passkey.",
    "privatliv": "Sådan behandler vi dine data, cookies og samtykke.",
}

# Images an admin may upload, recognised by their first bytes (the browser's content type is not trusted).
SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"))


def sniff(data: bytes) -> str | None:
    for magic, content_type in SIGNATURES:
        if data.startswith(magic) and (content_type != "image/webp" or data[8:12] == b"WEBP"):
            return content_type
    return None


def surface_for(path: str) -> str:
    """The surface of an app path: /login and /privatliv have their own image, everything else the default."""
    path = path.rstrip("/") or "/"
    return {"/login": "login", "/privatliv": "privatliv"}.get(path, "default")


# The generated images, by surface. Paths come only from this table, never from a request.
GENERATED: dict[str, str] = {name: os.path.join(STATIC, f"{name}.png") for name in SURFACES}


def generated(name: str) -> str:
    path = GENERATED.get(name)
    return path if path and os.path.isfile(path) else GENERATED["default"]


def _version(key: str | None, name: str) -> str:
    if key:
        return hashlib.sha256(key.encode()).hexdigest()[:10]
    return str(int(os.path.getmtime(generated(name))))


def resolve(name: str, overrides: dict | None) -> str | None:
    """The storage key that answers for a surface, or None for the generated image."""
    overrides = overrides or {}
    return overrides.get(name) or overrides.get("default")


def image_url(name: str, overrides: dict | None, page: dict | None = None) -> str:
    base = config.settings.origin.rstrip("/")
    if page and page.get("og_image_key"):
        return f"{base}/api/public/og/page-{page['id']}?v={_version(page['og_image_key'], 'default')}"
    return f"{base}/api/public/og/{name}?v={_version(resolve(name, overrides), name)}"


def meta_tags(*, title: str, description: str, site_name: str, image: str, url: str) -> str:
    """Open Graph and Twitter card tags. All values are escaped here."""
    t, d, s, i, u = (escape(v, quote=True) for v in (title, description, site_name, image, url))
    return (f'<meta property="og:title" content="{t}"><meta property="og:description" content="{d}">'
            f'<meta property="og:site_name" content="{s}"><meta property="og:type" content="website">'
            f'<meta property="og:url" content="{u}"><meta property="og:image" content="{i}">'
            f'<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
            f'<meta property="og:image:alt" content="{t}">'
            f'<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{t}">'
            f'<meta name="twitter:description" content="{d}"><meta name="twitter:image" content="{i}">')
