"""M1: normalize a raw filing document into a unified Document/Section shape.

Scope note (recorded per CLAUDE.md's "record deviations" instruction): this is
the text-native path only — HTML filings, split on "Item N." headers, which
covers the large majority of 10-K/10-Q/8-K filings. Table-aware parsing via
Docling, OCR fallback for scanned exhibits, and the schema-drift detector for
filings that don't match this shape are M2, not implemented here. When a
filing has no recognizable Item headers, `normalize_filing` returns a single
"Full Text" section rather than guessing — that's the honest fallback until
the M2 schema-drift detector exists to flag it properly.
"""

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Some 8-K exhibits are XBRL/XML rather than HTML; we deliberately still parse
# them as HTML for M1 (text-native path only, see module docstring) — this
# warning is expected, not a bug, so it's filtered rather than left noisy.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Matches lines like "Item 1A. Risk Factors" / "ITEM 7 - MD&A" / "Item 1.01 Entry into..."
ITEM_HEADER_RE = re.compile(r"(?im)^\s*(item\s+\d{1,2}[a-z]?(?:\.\d{2})?\.?\s*[-–—]?\s*[^\n]{0,120})\s*$")

MIN_SECTION_CHARS = 40  # drop near-empty "sections" that are just a stray header match


@dataclass(frozen=True)
class NormalizedSection:
    name: str
    index: int
    text: str


@dataclass(frozen=True)
class NormalizedDocument:
    accession_number: str
    form_type: str
    sections: list[NormalizedSection]


def _html_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse the runs of blank lines EDGAR's table-heavy HTML produces.
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _split_into_sections(text: str) -> list[NormalizedSection]:
    matches = list(ITEM_HEADER_RE.finditer(text))
    if not matches:
        return [NormalizedSection(name="Full Text", index=0, text=text.strip())]

    sections = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        name = " ".join(match.group(1).split())
        body = text[start:end].strip()
        if len(body) < MIN_SECTION_CHARS:
            continue
        sections.append(NormalizedSection(name=name, index=len(sections), text=body))

    if not sections:
        return [NormalizedSection(name="Full Text", index=0, text=text.strip())]
    return sections


def normalize_filing(raw_html: str, form_type: str, accession_number: str) -> NormalizedDocument:
    text = _html_to_text(raw_html)
    sections = _split_into_sections(text)
    return NormalizedDocument(accession_number=accession_number, form_type=form_type, sections=sections)
