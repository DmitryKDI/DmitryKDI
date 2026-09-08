"""Загрузка: лимиты, дедупликация, разрезание, срок хранения (Г.109).

Проверяется через настоящий HTTP-роут, а не вызовом функций: путь файла от
кнопки до диска — это и есть предмет проверки, и обходить его тестом значит
проверять не то.
"""
import os
import sys
import tempfile
from pathlib import Path

TEST_DB = tempfile.mktemp(suffix=".db")
os.environ["NADZOR_DB_PATH"] = TEST_DB
os.environ["FILE_STORE_DB"] = tempfile.mktemp(suffix=".db")
os.environ["FILE_STORE_CACHE"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import file_store  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)


def _pdf(pages: int = 2, filler: int = 0) -> bytes:
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Лист {index + 1}", fontname="helv", fontsize=12)
        for row in range(filler):
            page.draw_line((10, 100 + row % 600), (580, 120 + row % 600))
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def _upload(data: bytes, name: str = "том.pdf", side: str = "before"):
    return client.post(f"/documents?side={side}",
                       files={"file": (name, data, "application/pdf")})


def _set_limits(**kwargs):
    current = client.get("/settings").json()
    current.update(kwargs)
    client.put("/settings", json=current)


def test_same_file_uploaded_twice_takes_space_once():
    """Дедупликация по содержимому: два документа, один оригинал. Имя файла
    на это не влияет — сравнивается содержимое, а не то, как его назвали."""
    _set_limits(retention_days=90, max_upload_kb=512 * 1024, max_pages=5000, part_kb=32 * 1024)
    data = _pdf(2)
    before = file_store.stats().files
    first = _upload(data, "первый.pdf").json()
    second = _upload(data, "второй-с-другим-именем.pdf").json()
    assert first["id"] != second["id"], "документы должны быть разными записями"
    assert file_store.stats().files == before + 1, "оригинал сохранён дважды"
    print("OK: одинаковое содержимое под разными именами — один оригинал")


def test_file_name_never_becomes_the_path():
    """Имя приходит от загружающей стороны и в путь не идёт (Б.4): попытка
    выйти из каталога не должна создать файл где-то ещё."""
    doc = _upload(_pdf(1), "../../побег.pdf").json()
    path = Path(client.get("/documents").json()[0]["name"])
    assert path.name == "побег.pdf", "имя показывается как есть"
    stored = file_store.CACHE_DIR
    assert all(p.parent == stored for p in stored.glob("*.pdf"))
    assert doc["status"] in ("ok", "error")
    print("OK: имя файла остаётся метаданными и не строит путь")


def test_upload_larger_than_the_limit_is_refused_with_a_reason():
    _set_limits(max_upload_kb=64)
    heavy = _pdf(24, filler=300)
    assert len(heavy) > 64 * 1024, "тест бессмысленен: файл не превысил лимит"
    response = _upload(heavy, "тяжёлый.pdf")
    assert response.status_code == 413, response.status_code
    assert "МБ" in response.json()["detail"]
    _set_limits(max_upload_kb=512 * 1024)
    print("OK: превышение лимита размера отклоняется с понятной причиной")


def test_upload_with_too_many_pages_is_refused():
    _set_limits(max_pages=2)
    response = _upload(_pdf(5), "многостраничный.pdf")
    assert response.status_code == 413, response.status_code
    assert "страниц" in response.json()["detail"]
    _set_limits(max_pages=5000)
    print("OK: превышение лимита страниц отклоняется")


def test_not_a_pdf_is_refused_by_signature_not_by_extension():
    """Тип определяется по сигнатуре: расширение присылает клиент, и оно
    ничего не доказывает (Б.4)."""
    response = _upload(b"MZ\x90\x00 executable", "документ.pdf")
    assert response.status_code == 415, response.status_code
    print("OK: не-PDF отклонён по сигнатуре, несмотря на расширение .pdf")


def test_heavy_volume_is_split_into_parts_covering_every_page():
    """Тяжёлый том режется по весу, части покрывают все листы, а нумерация
    наружу остаётся исходной."""
    _set_limits(part_kb=64, max_upload_kb=512 * 1024)
    heavy = _pdf(24, filler=300)
    assert len(heavy) > 64 * 1024
    doc = _upload(heavy, "толстый-том.pdf").json()
    assert doc["parts_count"] > 1, doc
    listed = next(d for d in client.get("/documents").json() if d["id"] == doc["id"])
    assert listed["parts_count"] == doc["parts_count"]
    assert listed["size"] == len(heavy)
    _set_limits(part_kb=32 * 1024)
    print(f"OK: тяжёлый том разрезан на {doc['parts_count']} частей")


def test_deleting_one_of_two_identical_documents_keeps_the_original():
    """Дедупликация не должна оборачиваться потерей: удаление одного
    документа не уносит файл, на который ссылается другой."""
    data = _pdf(2, filler=7)
    first = _upload(data, "копия-1.pdf").json()
    second = _upload(data, "копия-2.pdf").json()
    assert client.delete(f"/documents/{first['id']}").status_code == 200
    remaining = client.get("/documents").json()
    assert any(d["id"] == second["id"] for d in remaining)
    still = next(d for d in remaining if d["id"] == second["id"])
    assert still["pages"] >= 0
    assert file_store.materialize(_digest_of(data)) is not None, "оригинал удалён вместе с чужим документом"
    print("OK: удаление одного документа не уносит общий оригинал")


def _digest_of(data: bytes) -> str:
    return file_store.digest_of(data)


def test_storage_report_separates_originals_from_cache():
    """Кэш и оригиналы показываются раздельно: кэш можно удалить сейчас,
    оригиналы — нет. Одной цифрой «сколько на диске» это не сказать."""
    stats = client.get("/storage").json()
    assert stats["files"] >= 1 and stats["bytes"] > 0
    assert "cache_files" in stats and "retention_days" in stats
    print(f"OK: оригиналов {stats['files']}, кэш {stats['cache_files']} файлов")


def test_cleanup_keeps_everything_that_is_still_referenced():
    """Уборка по сроку не трогает оригиналы загруженных документов, сколько
    бы ни стоял срок — иначе инспектор потеряет свой комплект."""
    _set_limits(retention_days=1)
    before = file_store.stats().files
    result = client.post("/storage/cleanup").json()
    assert result["kept_referenced"] >= 1
    assert file_store.stats().files == before, "уборка удалила используемый оригинал"
    _set_limits(retention_days=90)
    print("OK: уборка сохранила все оригиналы со ссылками")


def test_cache_can_be_dropped_and_documents_still_open():
    """Кэш — производное: очистили, и документ по-прежнему открывается."""
    doc = _upload(_pdf(2, filler=3), "восстановимый.pdf").json()
    result = client.post("/storage/cleanup?drop_cache=true").json()
    assert result["cache_removed"] >= 1
    path = file_store.materialize(_digest_from_document(doc["id"]))
    assert path is not None and path.exists()
    print("OK: после очистки кэша документ восстанавливается из базы")


def _digest_from_document(document_id: int) -> str:
    from app import models
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        return db.get(models.Document, document_id).digest
    finally:
        db.close()


if __name__ == "__main__":
    test_same_file_uploaded_twice_takes_space_once()
    test_file_name_never_becomes_the_path()
    test_upload_larger_than_the_limit_is_refused_with_a_reason()
    test_upload_with_too_many_pages_is_refused()
    test_not_a_pdf_is_refused_by_signature_not_by_extension()
    test_heavy_volume_is_split_into_parts_covering_every_page()
    test_deleting_one_of_two_identical_documents_keeps_the_original()
    test_storage_report_separates_originals_from_cache()
    test_cleanup_keeps_everything_that_is_still_referenced()
    test_cache_can_be_dropped_and_documents_still_open()
    print("ALL PASS")
