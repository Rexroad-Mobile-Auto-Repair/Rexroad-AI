from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter

from app.knowledge.extractors import PdfTextExtractor
from app.knowledge.service import KnowledgeService
from app.knowledge.store import KnowledgeStore
from app.policy.workspaces import WorkspaceRegistry


def pdf_bytes(*texts: str) -> bytes:
    page_start = 3
    font_number = page_start + len(texts)
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{ ' '.join(f'{page_start + i} 0 R' for i in range(len(texts))) }] /Count {len(texts)} >>".encode(),
        font_number: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, text in enumerate(texts):
        page_number = page_start + i
        content_number = font_number + 1 + i
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects[page_number] = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_number} 0 R >> >> /Contents {content_number} 0 R >>".encode()
        objects[content_number] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number in range(1, max(objects) + 1):
        offsets.append(len(output)); output.extend(f"{number} 0 obj\n".encode()); output.extend(objects[number]); output.extend(b"\nendobj\n")
    xref = len(output); output.extend(f"xref\n0 {max(objects) + 1}\n0000000000 65535 f \n".encode())
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(f"trailer\n<< /Size {max(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(output)


def make_service(tmp_path: Path, **limits) -> KnowledgeService:
    root = tmp_path / "workspace"; root.mkdir(exist_ok=True)
    return KnowledgeService(WorkspaceRegistry({"docs": root}), KnowledgeStore(tmp_path / "index.sqlite3"), **limits)


def test_pdf_extension_and_empty_pages_are_safe(tmp_path):
    path = tmp_path / "manual.pdf"; path.write_bytes(pdf_bytes("ignored"))
    extractor = PdfTextExtractor()
    assert extractor.supports(path)
    result = extractor.extract(path)
    assert result.source_type == "extracted_document"
    assert result.sections[0].content == "ignored"
    assert result.sections[0].page == 1


def test_malformed_pdf_is_skipped_without_crashing(tmp_path):
    root = tmp_path / "workspace"; root.mkdir()
    (root / "broken.pdf").write_bytes(b"not a pdf")
    assert make_service(tmp_path).index("docs").skipped_files == 1


def test_encrypted_pdf_is_skipped_without_leaking_parser_details(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret-password")
    output = BytesIO(); writer.write(output)
    root = tmp_path / "workspace"; root.mkdir()
    path = root / "encrypted.pdf"; path.write_bytes(output.getvalue())
    extractor = PdfTextExtractor()
    assert extractor.supports(path)
    service = make_service(tmp_path)
    result = service.index("docs")
    assert result.skipped_files == 1
    assert service._store.list_workspace("docs") == []
    assert "secret-password" not in result.model_dump_json()
    assert str(tmp_path) not in result.model_dump_json()


def test_valid_blank_pdf_is_safe_no_native_text_boundary(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO(); writer.write(output)
    root = tmp_path / "workspace"; root.mkdir()
    path = root / "blank.pdf"; path.write_bytes(output.getvalue())
    extracted = PdfTextExtractor().extract(path)
    assert extracted.sections == ()
    assert extracted.warnings == ("page_1_no_text",)
    service = make_service(tmp_path)
    result = service.index("docs")
    assert result.indexed_files == 1
    assert result.indexed_chunks == 0
    assert service._store.list_workspace("docs") == []


def test_pdf_limits_and_workspace_freshness(tmp_path):
    root = tmp_path / "workspace"; root.mkdir()
    path = root / "manual.pdf"; path.write_bytes(pdf_bytes("text"))
    service = make_service(tmp_path, max_source_bytes=4)
    assert service.index("docs").skipped_files == 1
