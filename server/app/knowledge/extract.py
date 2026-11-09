"""Text extraction for Viden: bytes → Markdown.

Office files (docx, pptx, xlsx) are ZIPs of XML, so the standard library reads them. PDFs use pypdf. Each PDF page
starts with a page marker (`<!-- page N -->`) that the chunker turns into the page of a citation.

Raises UnsupportedFormat for formats we do not read (the document is marked 'skipped') and BrokenFile for files
the file itself makes unreadable — encrypted, corrupt (marked 'failed', never retried).
"""

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

TEXT = {"txt", "text", "md", "markdown"}
SUPPORTED = TEXT | {"pdf", "docx", "pptx", "xlsx", "csv", "html", "htm"}

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv", "html": "text/html", "htm": "text/html",
    "md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain", "text": "text/plain",
}

PAGE_MARK = "<!-- page {n} -->"
PAGE_RE = re.compile(r"^<!-- page (\d+) -->$")
MAX_XLSX_ROWS = 5000


class UnsupportedFormat(Exception):
    pass


class BrokenFile(Exception):
    pass


@dataclass
class Extracted:
    markdown: str
    page_count: int = 0


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_supported(name: str) -> bool:
    return ext_of(name) in SUPPORTED


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(ext_of(name), "application/octet-stream")


def extract(data: bytes, filename: str) -> Extracted:
    ext = ext_of(filename)
    if ext not in SUPPORTED:
        raise UnsupportedFormat(f"Formatet .{ext or '?'} kan ikke læses")
    if ext in TEXT:
        return Extracted(_decode(data))
    if ext == "csv":
        return Extracted(_csv(_decode(data)))
    if ext in ("html", "htm"):
        return Extracted(html_to_markdown(_decode(data)))
    if ext == "pdf":
        return _pdf(data)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise BrokenFile("Filen er ødelagt eller krypteret") from e
    with zf:
        try:
            if ext == "docx":
                return Extracted(_docx(zf))
            if ext == "pptx":
                return _pptx(zf)
            return Extracted(_xlsx(zf))
        except (KeyError, ET.ParseError) as e:
            raise BrokenFile("Filen kunne ikke læses") from e


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _csv(text: str) -> str:
    rows = list(csv.reader(io.StringIO(text)))
    return "\n".join(" | ".join(c.strip() for c in row) for row in rows if any(c.strip() for c in row))


# ── HTML ───────────────────────────────────────────────────────────────────

class _Html(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "table", "ul", "ol", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.heading = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = int(tag[1])
            self.out.append("\n\n" + "#" * self.heading + " ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = 0
            self.out.append("\n\n")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data) if not self.heading else data.strip())


def html_to_markdown(html: str) -> str:
    p = _Html()
    p.feed(html)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── PDF ────────────────────────────────────────────────────────────────────

def _pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise BrokenFile("PDF'en er krypteret")
            except Exception as e:
                raise BrokenFile("PDF'en er krypteret") from e
        parts = []
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            parts.append(PAGE_MARK.format(n=i) + "\n\n" + text)
        return Extracted("\n\n".join(parts), page_count=len(reader.pages))
    except BrokenFile:
        raise
    except (PdfReadError, ValueError, KeyError) as e:
        raise BrokenFile("PDF'en er ødelagt") from e


# ── Office (Open XML) ──────────────────────────────────────────────────────

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _docx(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read("word/document.xml"))
    out = []
    for block in root.iter():
        if block.tag == f"{W}p":
            text = "".join(t.text or "" for t in block.iter(f"{W}t")).strip()
            if not text:
                continue
            style = block.find(f"{W}pPr/{W}pStyle")
            name = (style.get(f"{W}val") or "") if style is not None else ""
            m = re.match(r"(?i)(?:heading|overskrift)\s*(\d)", name)
            level = int(m.group(1)) if m else (1 if re.match(r"(?i)title|titel", name) else 0)
            out.append(("#" * level + " " + text) if level else text)
        elif block.tag == f"{W}tr":
            cells = ["".join(t.text or "" for t in tc.iter(f"{W}t")).strip() for tc in block.iter(f"{W}tc")]
            if any(cells):
                out.append(" | ".join(cells))
    return "\n\n".join(_dedupe_table_paragraphs(out))


def _dedupe_table_paragraphs(lines: list[str]) -> list[str]:
    # Paragraphs inside table cells are seen twice (as <w:p> and in the row); keep the row.
    rows = [l for l in lines if " | " in l]
    cell_text = {c.strip() for r in rows for c in r.split(" | ")}
    return [l for l in lines if " | " in l or l not in cell_text]


def _pptx(zf: zipfile.ZipFile) -> Extracted:
    names = sorted((n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                   key=lambda n: int(re.findall(r"\d+", n)[-1]))
    parts = []
    for i, name in enumerate(names, start=1):
        root = ET.fromstring(zf.read(name))
        paras = ["".join(t.text or "" for t in p.iter(f"{A}t")).strip() for p in root.iter(f"{A}p")]
        paras = [p for p in paras if p]
        title = paras[0] if paras else ""
        parts.append(PAGE_MARK.format(n=i) + f"\n\n## Slide {i}{': ' + title if title else ''}\n\n" + "\n".join(paras[1:]))
    return Extracted("\n\n".join(parts), page_count=len(names))


def _xlsx(zf: zipfile.ZipFile) -> str:
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{S}t")) for si in root.iter(f"{S}si")]
    sheet_names = {}
    if "xl/workbook.xml" in zf.namelist():
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        for i, sh in enumerate(wb.iter(f"{S}sheet"), start=1):
            sheet_names[i] = sh.get("name") or f"Ark {i}"
    sheets = sorted((n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                    key=lambda n: int(re.findall(r"\d+", n)[-1]))
    parts = []
    for name in sheets:
        idx = int(re.findall(r"\d+", name)[-1])
        root = ET.fromstring(zf.read(name))
        lines = []
        for row in root.iter(f"{S}row"):
            cells = []
            for c in row.iter(f"{S}c"):
                v = c.find(f"{S}v")
                if c.get("t") == "s" and v is not None and (v.text or "").isdigit():
                    cells.append(shared[int(v.text)] if int(v.text) < len(shared) else "")
                elif c.get("t") == "inlineStr":
                    cells.append("".join(t.text or "" for t in c.iter(f"{S}t")))
                else:
                    cells.append(v.text if v is not None and v.text else "")
            if any(x.strip() for x in cells):
                lines.append(" | ".join(x.strip() for x in cells))
            if len(lines) >= MAX_XLSX_ROWS:
                break
        parts.append(f"## {sheet_names.get(idx, f'Ark {idx}')}\n\n" + "\n".join(lines))
    return "\n\n".join(parts)
