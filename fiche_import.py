"""Import de fiches rédigées à la main (Markdown ou PDF) dans l'atelier."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


class FicheImportError(Exception):
    """Erreur lisible lors de l'import d'un fichier de fiche."""


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FicheImportError("Impossible de décoder le fichier texte : encodage non reconnu.")


def text_from_markdown_bytes(data: bytes) -> str:
    text = _decode_text(data).strip()
    if not text:
        raise FicheImportError("Le fichier Markdown est vide.")
    return text


def text_from_pdf_bytes(data: bytes) -> str:
    if not data:
        raise FicheImportError("Le fichier PDF est vide.")
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise FicheImportError(f"Fichier PDF illisible ({type(exc).__name__}).") from exc
    if not reader.pages:
        raise FicheImportError("Le PDF ne contient aucune page.")
    parts: list[str] = []
    for page in reader.pages:
        chunk = (page.extract_text() or "").strip()
        if chunk:
            parts.append(chunk)
    text = "\n\n".join(parts).strip()
    if not text:
        raise FicheImportError(
            "Aucun texte n'a pu être extrait du PDF (scan ou mise en page non reconnue). "
            "Exportez la fiche en Markdown ou copiez-collez le texte dans l'éditeur."
        )
    return text


def text_from_upload(filename: str | None, data: bytes) -> str:
    name = (filename or "").lower().strip()
    if name.endswith(".pdf"):
        return text_from_pdf_bytes(data)
    if name.endswith((".md", ".markdown", ".txt")):
        return text_from_markdown_bytes(data)
    if data[:5] == b"%PDF-":
        return text_from_pdf_bytes(data)
    return text_from_markdown_bytes(data)