"""A résumé or career document on disk (.txt/.md/.pdf/.docx)."""

from __future__ import annotations

from pathlib import Path

from .base import Source


class FileSource(Source):
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def fetch(self) -> str:
        if not self.path.is_file():
            raise FileNotFoundError(f"No such file: {self.path}")
        suffix = self.path.suffix.lower()
        if suffix in (".txt", ".md", ".markdown", ".rst"):
            return self.path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".pdf":
            return self._read_pdf()
        if suffix == ".docx":
            return self._read_docx()
        # Unknown extension: best-effort plain-text read.
        return self.path.read_text(encoding="utf-8", errors="replace")

    def _read_pdf(self) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError(
                "Reading PDF résumés needs 'pypdf'. Install with: pip install pypdf"
            ) from e
        reader = PdfReader(str(self.path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def _read_docx(self) -> str:
        try:
            import docx
        except ImportError as e:
            raise RuntimeError(
                "Reading .docx résumés needs 'python-docx'. "
                "Install with: pip install python-docx"
            ) from e
        document = docx.Document(str(self.path))
        return "\n".join(p.text for p in document.paragraphs)
