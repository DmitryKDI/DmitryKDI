import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.registry_diff as registry_diff  # noqa: E402
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


def test_run_triangulated_wires_room_and_equipment_cross_checks_into_escalation(tmp_path, monkeypatch, capsys):
    """Дымовой тест на саму цепочку run_triangulated() (Г.46) — до неё
    room_cross_check.py/equip_cross_check.py/triangulation.py/escalation.py
    были построены и покрыты собственными тестами, но ни разу не вызывались
    ни отсюда, ни из main.py. Реестр помещений и реестр оборудования — два
    РАЗНЫХ источника по двум РАЗНЫМ ключам ("140" и "М1"), у каждого только
    один сигнал, поэтому оба должны попасть в очередь эскалации (не
    confirmed — Г.30 п.4 требует ≥2 источников на ОДИН и тот же ключ), а не
    потеряться между отдельными --kind, как было раньше."""
    from app.documents import DocumentFacts

    pd_facts = DocumentFacts(
        name="pd.pdf", pages=1, text_facts=[],
        room_facts=[{"page": 1, "key": "140", "name": "Комната А", "area": "10"}],
        equipment_facts=[{"page": 1, "key": "М1", "name": "Насос", "qty": 2}],
    )
    rd_facts = DocumentFacts(
        name="rd.pdf", pages=1, text_facts=[],
        room_facts=[{"page": 1, "key": "140", "name": "Совсем другое помещение", "area": "10"}],
        equipment_facts=[{"page": 1, "key": "М1", "name": "Насос", "qty": 3}],
    )

    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    pd_path.touch()
    rd_path.touch()

    def fake_extract_document_facts(path, name):
        return pd_facts if str(path) == str(pd_path) else rd_facts

    monkeypatch.setattr(registry_diff, "extract_document_facts", fake_extract_document_facts)
    monkeypatch.setattr(registry_diff, "_load_text_facts", lambda paths: [])

    registry_diff.run_triangulated([str(pd_path)], [str(rd_path)], room_keys=[])

    out = capsys.readouterr().out
    assert "Кросс-проверка помещений" in out
    assert "Кросс-проверка оборудования" in out
    assert "не проверялся — не задан --rooms" in out
    assert "Триангуляция источников" in out
    assert "Подтверждено ≥2 источниками: 0" in out
    assert "Очередь эскалации (2)" in out
    assert "140" in out and "М1" in out
    print("OK: run_triangulated сводит реестры помещений/оборудования в общую триангуляцию и эскалацию")


def test_run_triangulated_auto_selects_routing_rooms_when_key_present_and_rooms_not_given(
    tmp_path, monkeypatch, capsys,
):
    """Комплексный прогон с ключом ИИ (Г.50): без явного --rooms граф
    маршрутизации раньше просто пропускался, даже когда ключ есть, — прямая
    претензия пользователя («система должна работать комплексно», не
    только по тексту). Без --rooms, но С ключом, routing_diff должен
    получить автоматически выбранные помещения — ТОЛЬКО те, что уже
    отмечены расхождением в реестре (Г.9), не весь комплект."""
    from app.documents import DocumentFacts
    from app.llm import LlmConfig

    pd_facts = DocumentFacts(
        name="pd.pdf", pages=1, text_facts=[],
        room_facts=[{"page": 1, "key": "140", "name": "Комната А", "area": "10"},
                    {"page": 1, "key": "150", "name": "Комната Б", "area": "12"}],
    )
    rd_facts = DocumentFacts(
        name="rd.pdf", pages=1, text_facts=[],
        room_facts=[{"page": 1, "key": "140", "name": "Другое имя", "area": "10"},
                    {"page": 1, "key": "150", "name": "Комната Б", "area": "12"}],
    )

    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    pd_path.touch()
    rd_path.touch()

    def fake_extract_document_facts(path, name):
        return pd_facts if str(path) == str(pd_path) else rd_facts

    routing_calls = []

    def fake_diff_room_routing(before_paths, after_paths, room_keys):
        routing_calls.append(list(room_keys))
        return {"renumbered": [], "retargeted": [], "connection_count_changed": [],
                "unchanged": [], "unusable": [], "room_only_before": [], "room_only_after": []}

    monkeypatch.setattr(registry_diff, "extract_document_facts", fake_extract_document_facts)
    monkeypatch.setattr(registry_diff, "_load_text_facts", lambda paths: [])
    monkeypatch.setattr(registry_diff, "extract_requirements_llm", lambda facts, cfg, **kw: [])
    monkeypatch.setattr(registry_diff, "diff_room_routing", fake_diff_room_routing)
    monkeypatch.setattr(registry_diff, "check_visual_candidates", lambda *a, **kw: [])
    monkeypatch.setattr(registry_diff, "verify_general_requirements_llm", lambda *a, **kw: [])

    fake_config = LlmConfig(provider="gigachat", api_key="fake", base_url="", model="")
    registry_diff.run_triangulated([str(pd_path)], [str(rd_path)], room_keys=[],
                                    requirements_llm_config=fake_config)

    out = capsys.readouterr().out
    assert routing_calls == [["140"]], routing_calls  # только "140" — расхождение в реестре, "150" совпало
    assert "--rooms не задан — граф маршрутизации автоматически проверен" in out
    print("OK: комплексный прогон с ключом сам выбирает помещения для routing_diff без --rooms")


if __name__ == "__main__":
    test_diff_splits_into_three_categories()
    test_diff_empty_sides_do_not_crash()
    test_load_text_facts_includes_catalog_pages_that_extract_document_facts_excludes()
    print("ALL PASS (запустите pytest для тестов с capsys/monkeypatch/tmp_path)")
