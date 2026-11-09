"""Heading-aware chunking of extracted Markdown (the algorithm from Surdej v1's OpsPilot).

Splits on Markdown headings, then packs paragraphs into chunks of about `max_tokens` with an overlap of
`overlap_tokens`, so context survives a chunk boundary. A block larger than a chunk (a wide table) is cut hard,
preferably at a newline or a space. Tokens are estimated at four characters each. Page markers from the extractor
set the page of the chunks that follow them.
"""

import re
from dataclasses import dataclass

from app.knowledge.extract import PAGE_RE

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    index: int
    content: str
    section: str
    page: int | None
    tokens: int


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _hard_split(text: str, max_chars: int) -> list[str]:
    pieces = []
    while len(text) > max_chars:
        cut = max(text.rfind("\n", 0, max_chars), text.rfind(" ", 0, max_chars))
        if cut < max_chars // 2:
            cut = max_chars
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


def chunk_markdown(markdown: str, max_tokens: int = 500, overlap_tokens: int = 50) -> list[Chunk]:
    max_chars, overlap_chars = max_tokens * 4, overlap_tokens * 4
    # (section, page, paragraph) in document order
    paragraphs: list[tuple[str, int | None, str]] = []
    section, page = "", None
    for block in re.split(r"\n\s*\n", markdown):
        block = block.strip()
        if not block:
            continue
        m = PAGE_RE.match(block)
        if m:
            page = int(m.group(1))
            continue
        lines = block.split("\n")
        h = HEADING_RE.match(lines[0])
        if h:
            section = h.group(2).strip()[:300]
            block = "\n".join(lines[1:]).strip()
            if not block:
                continue
        for piece in _hard_split(block, max_chars):
            paragraphs.append((section, page, piece))

    chunks: list[Chunk] = []
    buf: list[str] = []      # the overlap from the previous chunk (if any) + new paragraphs
    fresh = 0                # how many new paragraphs are in buf
    buf_section, buf_page = "", None

    def flush(keep_overlap: bool):
        nonlocal buf, fresh
        if fresh:
            text = "\n\n".join(buf).strip()
            chunks.append(Chunk(len(chunks), text, buf_section, buf_page, estimate_tokens(text)))
            tail = text[-overlap_chars:] if keep_overlap and overlap_chars and len(text) > overlap_chars else ""
            buf = [tail[tail.find(" ") + 1:]] if tail else []
        else:
            buf = []
        fresh = 0

    for sec, pg, para in paragraphs:
        if fresh and sec != buf_section:
            flush(keep_overlap=False)  # no overlap across sections
        if fresh and len("\n\n".join(buf)) + len(para) + 2 > max_chars:
            flush(keep_overlap=True)
        if not fresh:
            buf_section, buf_page = sec, pg
        buf.append(para)
        fresh += 1
    flush(keep_overlap=False)
    return chunks
