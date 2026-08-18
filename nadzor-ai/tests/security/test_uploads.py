"""Обработка загружаемых файлов: тип по сигнатуре, лимиты, пути."""
from __future__ import annotations

import pytest

from documents.intake import detect_type, new_file_id, storage_key
from documents.schemas import IntakeError

LIMITS = {"max_file_bytes": 1024, "allowed_signatures": ["pdf", "png"]}


def test_type_detected_by_signature_not_extension():
    kind, mime = detect_type(b"%PDF-1.7\n...")
    assert (kind, mime) == ("pdf", "application/pdf")


def test_unknown_signature_is_rejected():
    with pytest.raises(IntakeError):
        detect_type(b"\x00\x01\x02\x03unknown")


def test_executable_disguised_as_pdf_is_rejected():
    """Исполняемый файл с именем документа не принимается."""
    with pytest.raises(IntakeError):
        detect_type(b"MZ\x90\x00executable")


def test_size_limit():
    from documents.intake import check_limits
    with pytest.raises(IntakeError):
        check_limits(2048, LIMITS)
    with pytest.raises(IntakeError):
        check_limits(0, LIMITS)


def test_storage_key_is_built_from_own_identifier():
    """Имя файла не участвует в формировании пути."""
    key = storage_key("OBJ-001", new_file_id(), "pdf")
    assert key.startswith("objects/OBJ-001/")
    assert ".." not in key


def test_path_traversal_is_impossible():
    with pytest.raises(IntakeError):
        storage_key("../../etc", "abc123", "pdf")
    with pytest.raises(IntakeError):
        storage_key("OBJ-001", "../../../etc/passwd", "pdf")


def test_encrypted_pdf_is_reported_clearly():
    from documents.pdf import parse
    with pytest.raises(IntakeError) as info:
        parse(b"%PDF-1.4 broken")
    assert "PDF" in str(info.value)
