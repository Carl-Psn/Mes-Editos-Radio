"""Exports Word (.docx, python-docx) et PDF (fpdf2) des fiches et du dossier mensuel."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from fpdf import FPDF
from fpdf.enums import XPos, YPos

ACCENT = "#E2001A"
INK = "#111827"
MUTED = "#6B7280"
RULE = "#E5E7EB"

CATEGORY_COLORS = {
    "politique": "#E2001A",
    "eco": "#0F766E",
    "geopolitique": "#1E3A8A",
    "invite": "#7B2D5B",
    "autre": "#4B5563",
}

PRIORITY_LABELS = {1: "Secondaire", 2: "Important", 3: "Incontournable INSP"}

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"


# ------------------------------------------------------------------ dates et libellés


def format_date_fr(value: date | str, with_weekday: bool = True) -> str:
    day = date.fromisoformat(value[:10]) if isinstance(value, str) else value
    number = "1er" if day.day == 1 else str(day.day)
    text = f"{number} {_MOIS[day.month - 1]} {day.year}"
    return f"{_JOURS[day.weekday()]} {text}" if with_weekday else text


def month_label(month: str) -> str:
    """'2026-11' -> 'novembre 2026'."""
    year, number = month.split("-")
    return f"{_MOIS[int(number) - 1]} {year}"


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "document"


# ------------------------------------------------------------------ modèle de document


@dataclass
class FicheEntry:
    title: str
    show: str
    category: str
    date_label: str
    priority: int
    summary: str = ""
    author: str | None = None
    body: str = ""

    @property
    def color(self) -> str:
        return CATEGORY_COLORS.get(self.category, CATEGORY_COLORS["autre"])

    @property
    def meta(self) -> str:
        parts = [self.date_label]
        if self.author:
            parts.append(self.author)
        parts.append(f"Priorité {self.priority} - {PRIORITY_LABELS.get(self.priority, '')}".rstrip(" -"))
        return "  ·  ".join(parts)


@dataclass
class ExportDocument:
    title: str
    subtitle: str
    entries: list[FicheEntry] = field(default_factory=list)
    synthesis: str | None = None
    synthesis_title: str = "Synthèse du mois"
    cover: bool = False
    filename_stem: str = "export"


def entry_from_episode(episode: dict[str, Any]) -> FicheEntry:
    return FicheEntry(
        title=episode["title"],
        show=episode.get("show_title") or "Émission",
        category=episode.get("show_category") or "autre",
        date_label=format_date_fr(episode["pub_date"]),
        priority=int(episode.get("priority") or 1),
        summary=episode.get("summary") or "",
        author=episode.get("author"),
        body=(episode.get("fiche") or "").strip(),
    )


def single_fiche_document(episode: dict[str, Any]) -> ExportDocument:
    entry = entry_from_episode(episode)
    return ExportDocument(
        title=entry.title,
        subtitle=f"{entry.show} — {entry.date_label}",
        entries=[entry],
        filename_stem=f"fiche_{episode['pub_date']}_{slugify(entry.show)}",
    )


def category_document(show_title: str, month: str, episodes: list[dict[str, Any]]) -> ExportDocument:
    return ExportDocument(
        title=f"{show_title} — fiches prioritaires",
        subtitle=f"{month_label(month).capitalize()} · {_count_label(episodes)}",
        entries=[entry_from_episode(ep) for ep in episodes],
        cover=True,
        filename_stem=f"fiches_{slugify(show_title)}_{month}",
    )


def dossier_document(month: str, episodes: list[dict[str, Any]], synthesis: str | None) -> ExportDocument:
    """Dossier complet du mois : synthèse puis fiches regroupées par émission."""
    order: dict[int, int] = {}
    for ep in episodes:
        order.setdefault(ep["show_id"], len(order))
    ordered = sorted(episodes, key=lambda ep: (order[ep["show_id"]], ep["pub_date"], ep["published_at"]))
    return ExportDocument(
        title="Dossier des éditos prioritaires",
        subtitle=f"{month_label(month).capitalize()} · {_count_label(ordered)}",
        entries=[entry_from_episode(ep) for ep in ordered],
        synthesis=(synthesis or "").strip() or None,
        synthesis_title=f"Synthèse des tendances — {month_label(month)}",
        cover=True,
        filename_stem=f"dossier_insp_{month}",
    )


def _count_label(episodes: list[dict[str, Any]]) -> str:
    fiches = sum(1 for ep in episodes if (ep.get("fiche") or "").strip())
    label = f"{fiches} fiche{'s' if fiches > 1 else ''}"
    if fiches < len(episodes):
        label = f"{len(episodes)} épisodes, dont {label}"
    return label


# ------------------------------------------------------------- mini-analyseur Markdown

Run = tuple[str, bool, bool]  # (texte, gras, italique)


@dataclass
class Block:
    kind: str  # heading | bullet | number | para | quote
    runs: list[Run]
    level: int = 0
    marker: str = ""


_INLINE_RE = re.compile(
    r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*"
    r"|\*\*(?=\S)(.+?)(?<=\S)\*\*"
    r"|__(?=\S)(.+?)(?<=\S)__"
    r"|\*(?=\S)(.+?)(?<=\S)\*"
    r"|(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"
    r"|`([^`]+)`"
    r"|\[([^\]]+)\]\((https?://[^)\s]+)\)"
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+•]\s+(.*)$")
_NUMBER_RE = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_RULE_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?[\s:|-]+\|?$")


def parse_inline(text: str, bold: bool = False, italic: bool = False) -> list[Run]:
    runs: list[Run] = []
    position = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > position:
            runs.append((text[position : match.start()], bold, italic))
        both, strong, strong_alt, emphasis, emphasis_alt, code, link_text, link_url = match.groups()
        if both is not None:
            runs.extend(parse_inline(both, True, True))
        elif strong is not None or strong_alt is not None:
            runs.extend(parse_inline(strong if strong is not None else strong_alt, True, italic))
        elif emphasis is not None or emphasis_alt is not None:
            runs.extend(parse_inline(emphasis if emphasis is not None else emphasis_alt, bold, True))
        elif code is not None:
            runs.append((code, bold, italic))
        else:
            runs.append((f"{link_text} ({link_url})", bold, italic))
        position = match.end()
    if position < len(text):
        runs.append((text[position:], bold, italic))
    return [run for run in runs if run[0]]


def parse_markdown(text: str) -> list[Block]:
    """Convertit le Markdown simple produit par Gemini (titres, listes, gras, italique) en blocs."""
    blocks: list[Block] = []
    paragraph: list[str] = []
    indents: list[int] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("para", parse_inline(" ".join(paragraph))))
            paragraph.clear()

    def list_level(indent: int) -> int:
        while indents and indents[-1] > indent:
            indents.pop()
        if not indents or indents[-1] < indent:
            indents.append(indent)
        return min(len(indents) - 1, 2)

    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("```") or _RULE_RE.match(stripped):
            flush()
            indents.clear()
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush()
            indents.clear()
            title = re.sub(r"^\*\*(.+)\*\*$", r"\1", heading.group(2).strip())
            blocks.append(Block("heading", parse_inline(title), level=len(heading.group(1))))
            continue

        bullet = _BULLET_RE.match(line)
        if bullet and not stripped.startswith("**"):
            flush()
            level = list_level(len(bullet.group(1).expandtabs(4)))
            blocks.append(Block("bullet", parse_inline(bullet.group(2)), level=level))
            continue

        number = _NUMBER_RE.match(line)
        if number:
            flush()
            level = list_level(len(number.group(1).expandtabs(4)))
            blocks.append(Block("number", parse_inline(number.group(3)), level=level, marker=f"{number.group(2)}."))
            continue

        if stripped.startswith(">"):
            flush()
            indents.clear()
            blocks.append(Block("quote", parse_inline(stripped.lstrip("> ").strip())))
            continue

        if stripped.startswith("|"):
            flush()
            indents.clear()
            if not _TABLE_SEPARATOR_RE.match(stripped):
                cells = [cell.strip() for cell in stripped.strip("|").split("|")]
                blocks.append(Block("para", parse_inline(" — ".join(cell for cell in cells if cell))))
            continue

        if not paragraph and indents and blocks and blocks[-1].kind in ("bullet", "number") and raw[:1].isspace():
            blocks[-1].runs.extend(parse_inline(" " + stripped))
            continue

        indents.clear()
        paragraph.append(stripped)

    flush()
    return blocks


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _shows_in_order(entries: list[FicheEntry]) -> list[tuple[str, list[FicheEntry]]]:
    groups: dict[str, list[FicheEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.show, []).append(entry)
    return list(groups.items())


def _generated_on() -> str:
    return f"Document généré le {format_date_fr(datetime.now().date(), with_weekday=False)}"


# ------------------------------------------------------------------------- export Word

_DOCX_FONT = "Calibri"


def _docx_set_font(style: Any, name: str) -> None:
    """Impose une police à un style, y compris quand le modèle Word s'appuie sur les polices de thème."""
    style.font.name = name
    r_pr = style.element.get_or_add_rPr()
    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.append(r_fonts)
    for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if r_fonts.get(qn(attribute)) is not None:
            del r_fonts.attrib[qn(attribute)]
    for attribute in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        r_fonts.set(qn(attribute), name)


def _docx_page_field(paragraph: Any) -> None:
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            element = OxmlElement("w:fldChar")
            element.set(qn("w:fldCharType"), kind)
        else:
            element = OxmlElement("w:instrText")
            element.set(qn("xml:space"), "preserve")
            element.text = text
        run._r.append(element)
    run.font.size = Pt(8.5)
    run.font.color.rgb = RGBColor(*_rgb(MUTED))


def _docx_bottom_border(paragraph: Any, hex_color: str, size: int = 8) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), hex_color.lstrip("#"))
    borders.append(bottom)
    p_pr.append(borders)


def _docx_runs(paragraph: Any, runs: list[Run], color: str | None = None, size: float | None = None) -> None:
    for text, bold, italic in runs:
        run = paragraph.add_run(text)
        run.bold = bold or None
        run.italic = italic or None
        if color:
            run.font.color.rgb = RGBColor(*_rgb(color))
        if size:
            run.font.size = Pt(size)


def _docx_text(document: Any, text: str, *, size: float, color: str, bold: bool = False, italic: bool = False,
               after: float = 4, before: float = 0, align: Any = None) -> Any:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.space_before = Pt(before)
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor(*_rgb(color))
    return paragraph


def _docx_markdown(document: Any, markdown: str, heading_color: str) -> None:
    for block in parse_markdown(markdown):
        if block.kind == "heading":
            paragraph = document.add_heading(level=2 if block.level <= 2 else 3)
            _docx_runs(paragraph, [(text, False, italic) for text, _bold, italic in block.runs],
                       color=heading_color if block.level <= 2 else INK)
        elif block.kind == "bullet":
            style = ("List Bullet", "List Bullet 2", "List Bullet 3")[block.level]
            _docx_runs(document.add_paragraph(style=style), block.runs)
        elif block.kind == "number":
            paragraph = document.add_paragraph()
            indent = Cm(0.63 * (block.level + 1))
            paragraph.paragraph_format.left_indent = indent
            paragraph.paragraph_format.first_line_indent = -Cm(0.63)
            paragraph.paragraph_format.space_after = Pt(3)
            _docx_runs(paragraph, [(f"{block.marker} ", False, False), *block.runs])
        elif block.kind == "quote":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.8)
            _docx_runs(paragraph, [(text, bold, True) for text, bold, _italic in block.runs], color=MUTED)
        else:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            _docx_runs(paragraph, block.runs)


def _docx_entry(document: Any, entry: FicheEntry) -> None:
    kicker = _docx_text(document, entry.show.upper(), size=9, color=entry.color, bold=True, after=2)
    kicker.paragraph_format.keep_with_next = True
    title = document.add_heading(level=1)
    _docx_runs(title, [(entry.title, False, False)])
    meta = _docx_text(document, entry.meta, size=9.5, color=MUTED, after=6)
    meta.paragraph_format.keep_with_next = True
    if entry.summary:
        chapeau = _docx_text(document, entry.summary, size=10.5, color=MUTED, italic=True, after=8)
        chapeau.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _docx_bottom_border(chapeau, RULE, size=6)
    if entry.body:
        _docx_markdown(document, entry.body, entry.color)
    else:
        _docx_text(document, "Fiche non rédigée pour cet épisode : seul le résumé du flux RSS est disponible.",
                   size=10, color=MUTED, italic=True, before=6)


def build_docx(doc: ExportDocument) -> bytes:
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.3)
    section.top_margin = section.bottom_margin = Cm(2.2)

    normal = document.styles["Normal"]
    _docx_set_font(normal, _DOCX_FONT)
    normal.font.size = Pt(11)
    normal.font.color.rgb = RGBColor(*_rgb(INK))
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15
    for name, size, before, after in (("Heading 1", 18, 0, 4), ("Heading 2", 13, 14, 4), ("Heading 3", 11.5, 10, 3)):
        style = document.styles[name]
        _docx_set_font(style, _DOCX_FONT)
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.italic = False
        style.font.color.rgb = RGBColor(*_rgb(INK))
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
    for name in ("List Bullet", "List Bullet 2", "List Bullet 3"):
        document.styles[name].paragraph_format.space_after = Pt(3)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    label = footer.add_run("Éditos France Inter · Préparation INSP · page ")
    label.font.size = Pt(8.5)
    label.font.color.rgb = RGBColor(*_rgb(MUTED))
    _docx_page_field(footer)

    def page_break() -> None:
        document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    first_block = True
    if doc.cover:
        _docx_text(document, "FRANCE INTER  ·  PRÉPARATION AU CONCOURS DE L'INSP", size=9.5, color=ACCENT,
                   bold=True, before=90, after=10)
        _docx_text(document, doc.title, size=30, color=INK, bold=True, after=6)
        subtitle = _docx_text(document, doc.subtitle, size=14, color=MUTED, after=10)
        _docx_bottom_border(subtitle, ACCENT, size=18)
        _docx_text(document, _generated_on(), size=9.5, color=MUTED, before=8, after=24)

        _docx_text(document, "Sommaire", size=13, color=INK, bold=True, after=6)
        if doc.synthesis:
            _docx_text(document, doc.synthesis_title, size=10.5, color=INK, after=6)
        for show, entries in _shows_in_order(doc.entries):
            _docx_text(document, show.upper(), size=9, color=entries[0].color, bold=True, before=8, after=3)
            for entry in entries:
                line = document.add_paragraph()
                line.paragraph_format.space_after = Pt(2)
                line.paragraph_format.left_indent = Cm(0.4)
                _docx_runs(line, [(f"{entry.date_label}  ", False, False)], color=MUTED, size=10)
                _docx_runs(line, [(entry.title, False, False)], size=10)
        first_block = False

    if doc.synthesis:
        if not first_block:
            page_break()
        _docx_text(document, "SYNTHÈSE MENSUELLE", size=9, color=ACCENT, bold=True, after=2)
        title = document.add_heading(level=1)
        _docx_runs(title, [(doc.synthesis_title, False, False)])
        _docx_bottom_border(title, RULE, size=6)
        _docx_markdown(document, doc.synthesis, ACCENT)
        first_block = False

    for entry in doc.entries:
        if not first_block:
            page_break()
        _docx_entry(document, entry)
        first_block = False

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# -------------------------------------------------------------------------- export PDF

_FONT_DIRECTORIES = (
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/liberation2",
    "/usr/share/fonts/liberation-sans",
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/truetype/noto",
    "C:/Windows/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
)
# (normal, gras, italique, gras italique)
_FONT_FAMILIES = (
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
    ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf", "NotoSans-Italic.ttf", "NotoSans-BoldItalic.ttf"),
    ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Arial Bold Italic.ttf"),
)

# Repli sans police Unicode : les polices PDF de base ne couvrent que le Latin-1.
_LATIN1_FALLBACKS = str.maketrans(
    {
        "’": "'", "‘": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "‑": "-", "−": "-",
        "…": "...", "œ": "oe", "Œ": "OE", "€": "EUR", "•": "-", "\u202f": " ", "\u2009": " ",
        "\u200b": "", "≥": ">=", "≤": "<=", "→": "->", "≈": "~",
    }
)


def _find_font_files() -> tuple[str, str, str, str] | None:
    for directory in _FONT_DIRECTORIES:
        base = Path(directory)
        if not base.is_dir():
            continue
        for regular, bold, italic, bold_italic in _FONT_FAMILIES:
            if not (base / regular).is_file():
                continue
            bold_path = base / bold if (base / bold).is_file() else base / regular
            italic_path = base / italic if (base / italic).is_file() else base / regular
            bold_italic_path = base / bold_italic if (base / bold_italic).is_file() else bold_path
            return str(base / regular), str(bold_path), str(italic_path), str(bold_italic_path)
    return None


class _Pdf(FPDF):
    def __init__(self, running_title: str, has_cover: bool) -> None:
        super().__init__(format="A4", unit="mm")
        self.running_title = running_title
        self.has_cover = has_cover
        self.set_margins(22, 22, 22)
        self.set_auto_page_break(True, margin=20)
        self.face = "Helvetica"
        self.unicode_ok = False
        self._glyphs: Any = None
        files = _find_font_files()
        if files:
            try:
                for style, path in zip(("", "B", "I", "BI"), files):
                    self.add_font("Edito", style, path)
                self.face = "Edito"
                self.unicode_ok = True
                self._glyphs = self.fonts["edito"].cmap
            except Exception:
                self.face = "Helvetica"
                self.unicode_ok = False

    def clean(self, text: str) -> str:
        """Retire ou translittère les caractères que la police ne sait pas dessiner."""
        text = text.replace("\t", " ")
        if self.unicode_ok:
            text = text.replace("\u202f", "\u00a0")
            return "".join(ch for ch in text if ord(ch) in self._glyphs or ch in "\n ")
        return text.translate(_LATIN1_FALLBACKS).encode("latin-1", "ignore").decode("latin-1")

    def ink(self, style: str, size: float, color: str) -> None:
        self.set_font(self.face, style, size)
        self.set_text_color(*_rgb(color))

    def header(self) -> None:
        if self.has_cover and self.page_no() == 1:
            return
        self.ink("", 8, MUTED)
        self.set_y(11)
        self.cell(0, 5, self.clean("Éditos France Inter · Préparation INSP"), new_x=XPos.LMARGIN, new_y=YPos.TOP)
        self.cell(0, 5, self.clean(_ellipsis(self.running_title, 70)), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*_rgb(RULE))
        self.set_line_width(0.2)
        self.line(self.l_margin, 17.5, self.w - self.r_margin, 17.5)
        self.set_y(self.t_margin)

    def footer(self) -> None:
        if self.has_cover and self.page_no() == 1:
            return
        self.set_y(-14)
        self.ink("", 8, MUTED)
        self.cell(0, 5, f"{self.page_no()} / {{nb}}", align="C")

    def block(self, text: str, style: str, size: float, color: str, line_height: float, after: float = 0,
              align: str = "L") -> None:
        self.ink(style, size, color)
        self.multi_cell(0, line_height, self.clean(text), align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if after:
            self.ln(after)

    def runs(self, runs: list[Run], size: float, color: str, line_height: float, force_italic: bool = False) -> None:
        for text, bold, italic in runs:
            self.ink(("B" if bold else "") + ("I" if italic or force_italic else ""), size, color)
            self.write(line_height, self.clean(text))
        self.ln(line_height)

    def rule(self, color: str = RULE, width: float = 0.2, after: float = 4) -> None:
        self.set_draw_color(*_rgb(color))
        self.set_line_width(width)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(after)


def _ellipsis(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _pdf_markdown(pdf: _Pdf, markdown: str, heading_color: str) -> None:
    size, line_height = 10.5, 5.5
    base_margin = pdf.l_margin
    bullet = "•" if pdf.unicode_ok else "-"
    for block in parse_markdown(markdown):
        if block.kind == "heading":
            major = block.level <= 2
            pdf.ln(3.5 if major else 2)
            if pdf.will_page_break(22):
                pdf.add_page()
            text = "".join(run[0] for run in block.runs)
            pdf.block(text, "B", 12.5 if major else 11, heading_color if major else INK, 6.2 if major else 5.6, after=1.2)
        elif block.kind in ("bullet", "number"):
            indent = 3 + 5 * block.level
            marker = bullet if block.kind == "bullet" else block.marker
            pdf.ink("", size, INK)
            marker_width = max(4.5, pdf.get_string_width(pdf.clean(marker)) + 1.8)
            if pdf.will_page_break(line_height):
                pdf.add_page()
            pdf.set_x(base_margin + indent)
            pdf.cell(marker_width, line_height, pdf.clean(marker))
            pdf.set_left_margin(base_margin + indent + marker_width)
            pdf.runs(block.runs, size, INK, line_height)
            pdf.set_left_margin(base_margin)
            pdf.set_x(base_margin)
            pdf.ln(1)
        elif block.kind == "quote":
            pdf.set_left_margin(base_margin + 6)
            pdf.set_x(base_margin + 6)
            pdf.runs(block.runs, size, MUTED, line_height, force_italic=True)
            pdf.set_left_margin(base_margin)
            pdf.set_x(base_margin)
            pdf.ln(2)
        else:
            pdf.runs(block.runs, size, INK, line_height)
            pdf.ln(2)


def _pdf_entry(pdf: _Pdf, entry: FicheEntry) -> None:
    page, top = pdf.page_no(), pdf.get_y()
    pdf.block(entry.show.upper(), "B", 8.5, entry.color, 5, after=0.5)
    pdf.block(entry.title, "B", 17, INK, 7.6, after=1)
    pdf.block(entry.meta, "", 9, MUTED, 5, after=1.5)
    if pdf.page_no() == page:
        pdf.set_fill_color(*_rgb(entry.color))
        pdf.rect(pdf.l_margin - 5, top + 0.6, 1.3, pdf.get_y() - top - 2, style="F")
    if entry.summary:
        pdf.ln(1)
        pdf.block(entry.summary, "I", 10, MUTED, 5.2, after=3)
    pdf.rule(after=2)
    if entry.body:
        _pdf_markdown(pdf, entry.body, entry.color)
    else:
        pdf.ln(2)
        pdf.block("Fiche non rédigée pour cet épisode : seul le résumé du flux RSS est disponible.", "I", 10, MUTED, 5.2)


def build_pdf(doc: ExportDocument) -> bytes:
    pdf = _Pdf(running_title=doc.title, has_cover=doc.cover)
    pdf.set_title(doc.title)
    pdf.set_author("Éditos France Inter · Préparation INSP")
    pdf.add_page()

    first_block = True
    if doc.cover:
        pdf.set_fill_color(*_rgb(ACCENT))
        pdf.rect(0, 0, pdf.w, 7, style="F")
        pdf.set_y(62)
        pdf.block("FRANCE INTER  ·  PRÉPARATION AU CONCOURS DE L'INSP", "B", 9.5, ACCENT, 5, after=5)
        pdf.block(doc.title, "B", 29, INK, 12.5, after=3)
        pdf.block(doc.subtitle, "", 14, MUTED, 7, after=5)
        pdf.rule(color=ACCENT, width=0.8, after=5)
        pdf.block(_generated_on(), "", 9.5, MUTED, 5, after=12)

        pdf.block("Sommaire", "B", 13, INK, 6.5, after=2)
        if doc.synthesis:
            pdf.block(doc.synthesis_title, "", 10, INK, 5.4, after=1)
        for show, entries in _shows_in_order(doc.entries):
            pdf.ln(2.5)
            if pdf.will_page_break(14):
                pdf.add_page()
            pdf.block(show.upper(), "B", 8.5, entries[0].color, 5, after=0.8)
            for entry in entries:
                if pdf.will_page_break(5.4):
                    pdf.add_page()
                base_margin = pdf.l_margin
                pdf.set_left_margin(base_margin + 3)
                pdf.set_x(base_margin + 3)
                pdf.ink("", 9.5, MUTED)
                pdf.write(5.2, pdf.clean(f"{entry.date_label}   "))
                pdf.ink("", 9.5, INK)
                pdf.write(5.2, pdf.clean(entry.title))
                pdf.ln(5.2)
                pdf.set_left_margin(base_margin)
                pdf.set_x(base_margin)
        first_block = False

    if doc.synthesis:
        if not first_block:
            pdf.add_page()
        pdf.block("SYNTHÈSE MENSUELLE", "B", 8.5, ACCENT, 5, after=0.5)
        pdf.block(doc.synthesis_title, "B", 17, INK, 7.6, after=3)
        pdf.rule(after=2)
        _pdf_markdown(pdf, doc.synthesis, ACCENT)
        first_block = False

    for entry in doc.entries:
        if not first_block:
            pdf.add_page()
        _pdf_entry(pdf, entry)
        first_block = False

    return bytes(pdf.output())
