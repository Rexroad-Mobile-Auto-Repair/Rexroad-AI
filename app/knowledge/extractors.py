from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader


@dataclass(frozen=True)
class ExtractedSection:
    content: str
    section: str | None = None
    page: int | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    source_type: str
    sections: tuple[ExtractedSection, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


class DocumentExtractor(Protocol):
    def supports(self, path: Path) -> bool: ...

    def extract(self, path: Path) -> ExtractedDocument: ...


class PlainTextExtractor:
    def supports(self, path: Path) -> bool:
        return path.name.casefold().endswith((".extract.txt", ".extracted.txt"))

    def extract(self, path: Path) -> ExtractedDocument:
        content = path.read_text(encoding="utf-8")
        sections = tuple(
            ExtractedSection(part.strip(), section=str(index))
            for index, part in enumerate(content.split("\f"), 1)
            if part.strip()
        )
        return ExtractedDocument(source_type="extracted_document", sections=sections)


class PdfTextExtractor:
    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() == ".pdf"

    def extract(self, path: Path) -> ExtractedDocument:
        reader = PdfReader(str(path), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted PDF is unsupported")
        sections: list[ExtractedSection] = []
        warnings: list[str] = []
        for page_number, page in enumerate(reader.pages, 1):
            text = " ".join((page.extract_text() or "").split())
            if text:
                sections.append(ExtractedSection(text, section=str(page_number), page=page_number))
            else:
                warnings.append(f"page_{page_number}_no_text")
        return ExtractedDocument(
            source_type="extracted_document",
            sections=tuple(sections),
            warnings=tuple(warnings),
            metadata={"format": "pdf", "total_pages": str(len(reader.pages)), "extracted_pages": str(len(sections))},
        )


class DocumentExtractorRegistry:
    def __init__(self, extractors: tuple[DocumentExtractor, ...] = (PlainTextExtractor(), PdfTextExtractor())) -> None:
        self._extractors = extractors

    def for_path(self, path: Path) -> DocumentExtractor | None:
        return next((extractor for extractor in self._extractors if extractor.supports(path)), None)
