"""Conversion bidirectionnelle entre le Markdown stocké en base et le HTML de l'éditeur de fiches."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup, NavigableString, Tag

# Couleurs proposées dans l'éditeur (libellés affichés côté composant).
EDITOR_COLORS: tuple[tuple[str, str], ...] = (
    ("#111827", "Noir"),
    ("#E2001A", "Rouge"),
    ("#2563EB", "Bleu"),
    ("#15803D", "Vert"),
    ("#7C3AED", "Violet"),
    ("#CA8A04", "Or"),
)

_INLINE_HTML_RE = re.compile(
    r"<u>(?P<u>.+?)</u>"
    r"|<span\s+style=['\"]color:\s*(?P<color>[^;'\"]+)['\"]\s*>(?P<c>.+?)</span>"
    r"|\*\*\*(?=\S)(?P<bi>.+?)(?<=\S)\*\*\*"
    r"|\*\*(?=\S)(?P<b>.+?)(?<=\S)\*\*"
    r"|\*(?=\S)(?P<i>.+?)(?<=\S)\*"
    r"|`(?P<code>[^`]+)`",
    re.DOTALL,
)


def _rgb_to_hex(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("#"):
        rgb = value[1:]
        if len(rgb) == 3:
            return "#" + "".join(ch * 2 for ch in rgb)
        if len(rgb) == 6:
            return f"#{rgb}"
    match = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value)
    if match:
        return "#{:02x}{:02x}{:02x}".format(*(int(match.group(i)) for i in range(1, 4)))
    return value


def unescape_text(text: str) -> str:
    """Remplace les entités HTML résiduelles (ex. &#x27;, &quot;) par les vrais caractères."""
    if not text or "&" not in text:
        return text
    previous = None
    current = text
    while previous != current:
        previous = current
        current = html.unescape(current)
    return current


def _html_text(text: str) -> str:
    """Échappe uniquement ce qui est dangereux en HTML ; les apostrophes et guillemets restent lisibles."""
    return html.escape(text, quote=False)


def leading_indent_level(leading: str) -> int:
    """Niveau de puce (0–2) à partir des espaces en tête de ligne (2 ou 4 espaces par niveau)."""
    spaces = len((leading or "").expandtabs(4))
    if spaces >= 8:
        return 2
    if spaces >= 4:
        return 1
    if spaces >= 2:
        return 1
    return 0


def normalize_color(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "#111827"
    return _rgb_to_hex(value) if not value.startswith("#") else _rgb_to_hex(value)


def _inline_html(text: str) -> str:
    text = unescape_text(text)
    if not text:
        return ""
    parts: list[str] = []
    pos = 0
    for match in _INLINE_HTML_RE.finditer(text):
        if match.start() > pos:
            parts.append(_html_text(text[pos : match.start()]))
        if match.group("u") is not None:
            parts.append(f"<u>{_inline_html(match.group('u'))}</u>")
        elif match.group("c") is not None:
            color = normalize_color(match.group("color") or "")
            parts.append(f'<span style="color:{html.escape(color)}">{_inline_html(match.group("c"))}</span>')
        elif match.group("bi") is not None:
            parts.append(f"<strong><em>{_inline_html(match.group('bi'))}</em></strong>")
        elif match.group("b") is not None:
            parts.append(f"<strong>{_inline_html(match.group('b'))}</strong>")
        elif match.group("i") is not None:
            parts.append(f"<em>{_inline_html(match.group('i'))}</em>")
        elif match.group("code") is not None:
            parts.append(f"<code>{_html_text(match.group('code'))}</code>")
        pos = match.end()
    if pos < len(text):
        parts.append(_html_text(text[pos:]))
    return "".join(parts)


def markdown_to_html(markdown: str) -> str:
    """Prépare le contenu pour l'éditeur visuel (HTML simple, sans Markdown visible)."""
    lines = unescape_text(markdown or "").replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    para: list[str] = []
    in_list = False

    def flush_para() -> None:
        if para:
            body = _inline_html(" ".join(para))
            blocks.append(f"<p>{body}</p>" if body else "<p><br></p>")
            para.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            blocks.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_para()
            close_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", stripped)
        if heading:
            flush_para()
            close_list()
            level = min(len(heading.group(1)), 2)
            tag = f"h{level}"
            blocks.append(f"<{tag}>{_inline_html(heading.group(2).strip())}</{tag}>")
            continue

        bullet = re.match(r"^(\s*)[-*+•]\s+(.*)$", line)
        if bullet:
            flush_para()
            level = leading_indent_level(bullet.group(1))
            indent_class = f' class="ql-indent-{level}"' if level else ""
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append(
                f'<li data-indent="{level}"{indent_class}>{_inline_html(bullet.group(2).strip())}</li>'
            )
            continue

        if stripped.startswith(">"):
            flush_para()
            close_list()
            blocks.append(f"<blockquote>{_inline_html(stripped.lstrip('> ').strip())}</blockquote>")
            continue

        close_list()
        para.append(stripped)

    flush_para()
    close_list()
    body = "".join(blocks)
    return body or "<p><br></p>"


def _li_indent(node: Tag) -> int:
    if node.has_attr("data-indent"):
        return min(int(node["data-indent"]), 2)
    classes = node.get("class") or []
    for name in classes:
        if name.startswith("ql-indent-"):
            return min(int(name.split("-")[-1]), 2)
    return 0


def _inline_to_markdown(node: Tag | NavigableString) -> str:
    if isinstance(node, NavigableString):
        return str(node).replace("\xa0", " ")

    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()
    if name in {"strong", "b"}:
        return f"**{''.join(_inline_to_markdown(child) for child in node.children)}**"
    if name in {"em", "i"}:
        return f"*{''.join(_inline_to_markdown(child) for child in node.children)}*"
    if name == "u":
        return f"<u>{''.join(_inline_to_markdown(child) for child in node.children)}</u>"
    if name == "span":
        style = node.get("style", "")
        color_match = re.search(r"color:\s*([^;]+)", style, re.I)
        inner = "".join(_inline_to_markdown(child) for child in node.children)
        if color_match:
            color = normalize_color(color_match.group(1))
            return f'<span style="color:{color}">{inner}</span>'
        return inner
    if name == "code":
        return f"`{''.join(_inline_to_markdown(child) for child in node.children)}`"
    if name == "br":
        return "\n"
    return "".join(_inline_to_markdown(child) for child in node.children)


def _block_to_markdown(node: Tag, blocks: list[str]) -> None:
    name = node.name.lower()
    if name in {"h1", "h2"}:
        level = 1 if name == "h1" else 2
        text = "".join(_inline_to_markdown(child) for child in node.children).strip()
        if text:
            blocks.append(f"{'#' * level} {text}")
        return

    if name == "p":
        text = "".join(_inline_to_markdown(child) for child in node.children).strip()
        if text:
            blocks.append(text)
        return

    if name == "blockquote":
        text = "".join(_inline_to_markdown(child) for child in node.children).strip()
        if text:
            for line in text.splitlines() or [text]:
                line = line.strip()
                if line:
                    blocks.append(f"> {line}")
        return

    if name == "ul":
        for child in node.children:
            if isinstance(child, Tag) and child.name.lower() == "li":
                _list_item_to_markdown(child, blocks)
        return

    if name == "li":
        _list_item_to_markdown(node, blocks)
        return

    if name == "div":
        for child in node.children:
            if isinstance(child, Tag):
                _block_to_markdown(child, blocks)
        return

    text = "".join(_inline_to_markdown(child) for child in node.children).strip()
    if text:
        blocks.append(text)


def _list_item_to_markdown(node: Tag, blocks: list[str], depth: int | None = None) -> None:
    indent = depth if depth is not None else _li_indent(node)
    text = "".join(_inline_to_markdown(child) for child in node.children if not isinstance(child, Tag) or child.name.lower() != "ul").strip()
    # Nested sublists inside the same <li>.
    for child in node.children:
        if isinstance(child, Tag) and child.name.lower() == "ul":
            if text:
                blocks.append(f"{'    ' * indent}- {text}")
                text = ""
            for sub in child.children:
                if isinstance(sub, Tag) and sub.name.lower() == "li":
                    _list_item_to_markdown(sub, blocks, depth=min(indent + 1, 2))
            return
    blocks.append(f"{'    ' * indent}- {text}" if text else f"{'    ' * indent}-")


def _is_list_line(line: str) -> bool:
    stripped = line.lstrip()
    return bool(re.match(r"^[-*+•]\s", stripped)) or line.startswith(("    ", "\t"))


def html_to_markdown(fragment: str) -> str:
    """Ramène le HTML de l'éditeur vers le Markdown interne (titres, listes, styles en ligne)."""
    text = unescape_text((fragment or "").strip())
    if not text or text in {"<p><br></p>", "<p></p>", "<br>"}:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    blocks: list[str] = []
    roots = soup.body.children if soup.body else soup.children
    for child in roots:
        if isinstance(child, Tag):
            _block_to_markdown(child, blocks)
        elif isinstance(child, NavigableString) and str(child).strip():
            blocks.append(str(child).strip())
    cleaned: list[str] = []
    for block in blocks:
        piece = block.rstrip()
        if not piece.strip():
            continue
        if piece.lstrip().startswith(("-", ">", "#")) or piece.startswith("    "):
            cleaned.append(piece)
        else:
            cleaned.append(piece.strip())
    merged: list[str] = []
    for piece in cleaned:
        if merged and _is_list_line(piece) and _is_list_line(merged[-1]):
            merged[-1] = f"{merged[-1]}\n{piece}"
        else:
            merged.append(piece)
    return unescape_text("\n\n".join(merged))


def preview_html(markdown: str) -> str:
    """HTML pour l'onglet prévisualisation (proche du rendu export)."""
    return markdown_to_html(markdown)