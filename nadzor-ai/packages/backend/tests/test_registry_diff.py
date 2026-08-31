import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.registry_diff import _diff, _load_text_facts, _registry  # noqa: E402

SAMPLE_DIR = Path("/home/user/nadzor_sample")
RD_OV1_B = SAMPLE_DIR / "АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-101-676.pdf"


def test_diff_splits_into_three_categories():
    before = {"101": [], "102": [], "103": []}
    after = {"102": [], "103": [], "104": []}
    both, only_before, only_after = _diff(before, after)
    assert both == {"102", "103"}
    assert only_before == {"101"}
    assert only_after == {"104"}
    print("OK: три категории (Г.9) считаются чистым пересечением/разностью множеств")


def test_diff_empty_sides_do_not_crash():
    both, only_before, only_after = _diff({}, {})
    assert both == only_before == only_after == set()
    print("OK: пустые реестры не падают, дают пустые категории")


def test_registry_skips_missing_file_without_crashing(capsys):
    """Битый/отсутствующий файл не должен ронять весь прогон — реальная
    ситуация: неверный путь, файл переименован между запусками."""
    reg = _registry(["/nonexistent/path/does-not-exist.pdf"], "room_facts")
    assert reg == {}
    captured = capsys.readouterr()
    assert "не найден" in captured.err
    print("OK: отсутствующий файл пропускается с предупреждением, не падает")


def test_load_text_facts_skips_missing_file_without_crashing(capsys):
    facts = _load_text_facts(["/nonexistent/path/does-not-exist.pdf"])
    assert facts == []
    captured = capsys.readouterr()
    assert "не найден" in captured.err
    print("OK: _load_text_facts тоже пропускает отсутствующий файл, не падает")


def test_load_text_facts_includes_catalog_pages_that_extract_document_facts_excludes():
    """Реальное измерение (Г.34): таблица подбора вентиляторов «Проект:
    <модель>» (единственный текстовый след кода системы ВД/ПД в РД)
    физически подшита внутри 419-страничного каталога поставщика в этом
    же файле. `extract_document_facts`/material.py правильно исключают
    такие страницы из реестров помещений/оборудования — но требование
    Г.33/requirement_cross_check.py ищет только присутствие короткого
    кода-токена, для чего фильтр каталога — потеря сигнала, не защита от
    шума. `_load_text_facts` должен видеть БОЛЬШЕ страниц, чем
    `facts.text_facts` того же файла."""
    if not RD_OV1_B.exists():
        print("SKIP: нет файла", RD_OV1_B)
        return
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from app.documents import extract_document_facts

    full = _load_text_facts([str(RD_OV1_B)])
    filtered = extract_document_facts(str(RD_OV1_B), RD_OV1_B.name)
    assert len(full) > len(filtered.text_facts)
    full_pages = {f["page"] for f in full}
    excluded_pages = set(filtered.excluded)
    assert full_pages & excluded_pages, "ожидали, что _load_text_facts захватит хотя бы часть исключённых material.py страниц"
    print(f"OK: _load_text_facts видит {len(full)} страниц против {len(filtered.text_facts)} "
          f"у facts.text_facts (исключено material.py: {len(filtered.excluded)})")


if __name__ == "__main__":
    test_diff_splits_into_three_categories()
    test_diff_empty_sides_do_not_crash()
    test_load_text_facts_includes_catalog_pages_that_extract_document_facts_excludes()
    print("ALL PASS (запустите pytest для теста с capsys)")
