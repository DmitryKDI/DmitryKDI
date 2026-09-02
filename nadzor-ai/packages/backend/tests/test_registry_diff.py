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


# --------------------------------------------------------------------------
# run_page_pair_comparison (Г.51) — прямое сравнение листов ПД↔РД
# --------------------------------------------------------------------------

def _pair_doc(name, text, room_key=None, room_name="А", page_kind="text"):
    from app.matching import DocumentInput
    room_facts = [{"page": 1, "key": room_key, "name": room_name}] if room_key else []
    return DocumentInput(name=name, pages=1, text_facts=[{"page": 1, "text": text}],
                         room_facts=room_facts, page_kinds={1: page_kind})


def test_page_pair_comparison_skips_pair_with_matching_registries(monkeypatch):
    """Уровень 0 (Г.23/25/28, router.py): реестры страницы совпали —
    пропуск без единого обращения к ИИ. router.py был построен и
    протестирован ещё в другой сессии, но ни разу не вызывался ни отсюда,
    ни из main.py (Г.46) — это первое реальное подключение."""
    before = [_pair_doc("pd.pdf", "Помещение 140 отопление", room_key="140", room_name="Венткамера")]
    after = [_pair_doc("rd.pdf", "Помещение 140 отопление", room_key="140", room_name="Венткамера")]

    calls = []

    def fake_compare_text_pair(*a, **kw):
        calls.append(a)
        return {"significant": []}

    monkeypatch.setattr(registry_diff, "compare_text_pair", fake_compare_text_pair)
    results = registry_diff.run_page_pair_comparison(
        before, ["pd.pdf"], after, ["rd.pdf"], config=None)

    assert calls == [], "реестры совпали — пара уровня 0, ИИ не должен вызываться"
    assert results == []
    print("OK: пара с совпавшими реестрами (уровень 0) пропущена без вызова ИИ")


def test_page_pair_comparison_calls_llm_on_mismatched_registry(monkeypatch):
    """Уровень 2: реестр помещений страницы не совпал (разное название) —
    ИИ обязателен, находки из `significant` (только с change=true)
    попадают в результат."""
    before = [_pair_doc("pd.pdf", "Помещение 140 отопление", room_key="140", room_name="Венткамера")]
    after = [_pair_doc("rd.pdf", "Помещение 140 отопление", room_key="140", room_name="Насосная")]

    def fake_compare_text_pair(before_text, after_text, config, context="", discipline=None, timeout=120.0):
        assert "140" in before_text
        return {"significant": [
            {"label": "Название", "change": "Венткамера -> Насосная", "severity": "существенно"},
            {"label": "Шум", "change": ""},  # change="" -> не находка
        ]}

    monkeypatch.setattr(registry_diff, "compare_text_pair", fake_compare_text_pair)
    results = registry_diff.run_page_pair_comparison(
        before, ["pd.pdf"], after, ["rd.pdf"], config=None)

    assert len(results) == 1
    assert results[0]["status"] == "ok"
    assert results[0]["level"] == 2
    assert len(results[0]["findings"]) == 1
    assert results[0]["findings"][0]["change"] == "Венткамера -> Насосная"
    print("OK: расхождение в реестре — уровень 2, ИИ вызван, находка с change извлечена")


def test_page_pair_comparison_reports_failed_call_not_silent(monkeypatch):
    """Сорванный вызов (сеть, провайдер) не роняет прогон и не выглядит
    как «различий нет» — status="error" с причиной (Г.10)."""
    before = [_pair_doc("pd.pdf", "Помещение 5", room_key="5", room_name="А")]
    after = [_pair_doc("rd.pdf", "Помещение 5", room_key="5", room_name="Б")]

    def fake_compare_text_pair(*a, **kw):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(registry_diff, "compare_text_pair", fake_compare_text_pair)
    results = registry_diff.run_page_pair_comparison(
        before, ["pd.pdf"], after, ["rd.pdf"], config=None)

    assert len(results) == 1
    assert results[0]["status"] == "error"
    assert "сеть недоступна" in results[0]["error"]
    assert results[0]["findings"] == []
    print("OK: сорванный вызов даёт видимый status=error, не пустой молчаливый результат")


def test_page_pair_comparison_captures_injection_suspected(monkeypatch):
    before = [_pair_doc("pd.pdf", "Помещение 9", room_key="9", room_name="А")]
    after = [_pair_doc("rd.pdf", "Помещение 9", room_key="9", room_name="Б")]

    monkeypatch.setattr(registry_diff, "compare_text_pair",
                        lambda *a, **kw: {"significant": [], "injection_suspected": True})
    results = registry_diff.run_page_pair_comparison(
        before, ["pd.pdf"], after, ["rd.pdf"], config=None)

    assert results[0]["injection_suspected"] is True
    print("OK: injection_suspected из ответа модели прокидывается в результат")


def test_page_pair_comparison_on_result_fires_per_pair(monkeypatch):
    before = [_pair_doc("pd.pdf", "Помещение 7", room_key="7", room_name="А")]
    after = [_pair_doc("rd.pdf", "Помещение 7", room_key="7", room_name="Б")]
    seen = []

    monkeypatch.setattr(registry_diff, "compare_text_pair", lambda *a, **kw: {"significant": []})
    registry_diff.run_page_pair_comparison(
        before, ["pd.pdf"], after, ["rd.pdf"], config=None, on_result=seen.append)

    assert len(seen) == 1 and seen[0]["level"] == 2
    print("OK: on_result вызывается по мере готовности каждой пары")


def test_page_pair_comparison_promotes_visually_different_drawing_despite_matching_registries(monkeypatch, tmp_path):
    """Г.54 — реальный пропущенный случай слепого прогона: реестры
    помещений совпали (тот же номер, то же название), но САМ чертёж
    (воздуховодная обвязка и т.п.) отличается — ни room_facts, ни
    equipment_facts эту разницу в принципе не видят. Пиксельный предфильтр
    (без ИИ) должен промоутировать такую пару уровня 0 в очередь на зрение
    вместо молчаливого пропуска."""
    import pymupdf

    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    doc = pymupdf.open()
    doc.new_page(width=400, height=400)
    doc.save(str(pd_path))
    doc.close()
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    page.draw_rect(pymupdf.Rect(0, 0, 400, 400), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(str(rd_path))
    doc.close()

    before = [_pair_doc(pd_path.name, "Помещение 140", room_key="140", room_name="Венткамера", page_kind="drawing")]
    after = [_pair_doc(rd_path.name, "Помещение 140", room_key="140", room_name="Венткамера", page_kind="drawing")]

    calls = []
    monkeypatch.setattr(registry_diff, "compare_page_pair",
                        lambda *a, **kw: (calls.append(1), {"significant": []})[1])
    results = registry_diff.run_page_pair_comparison(
        before, [str(pd_path)], after, [str(rd_path)], config=None)

    assert len(calls) == 1, "визуально другой лист уровня 0 должен уйти в ИИ, а не быть пропущен молча"
    assert len(results) == 1
    assert results[0]["level"] == 0
    assert results[0]["promoted_by_visual_diff"] is True
    print("OK: визуально другой лист с совпавшими реестрами промоутирован пиксельным предфильтром (Г.54)")


def test_page_pair_comparison_skips_visually_identical_drawing_at_level_zero(monkeypatch, tmp_path):
    """Обратная сторона Г.54: реестры совпали И рендеры визуально
    неотличимы — пропуск без ИИ остаётся в силе, предфильтр не разгоняет
    бюджет там, где картинка реально та же."""
    import pymupdf

    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    for path in (pd_path, rd_path):
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=400)
        page.draw_rect(pymupdf.Rect(50, 50, 150, 150), color=(0, 0, 0), fill=(0, 0, 0))
        doc.save(str(path))
        doc.close()

    before = [_pair_doc(pd_path.name, "Помещение 140", room_key="140", room_name="Венткамера", page_kind="drawing")]
    after = [_pair_doc(rd_path.name, "Помещение 140", room_key="140", room_name="Венткамера", page_kind="drawing")]

    calls = []
    monkeypatch.setattr(registry_diff, "compare_page_pair", lambda *a, **kw: (calls.append(1), None)[1])
    results = registry_diff.run_page_pair_comparison(
        before, [str(pd_path)], after, [str(rd_path)], config=None)

    assert calls == [], "визуально одинаковый лист уровня 0 не должен вызывать ИИ"
    assert results == []
    print("OK: визуально неотличимый лист уровня 0 по-прежнему пропущен без вызова ИИ")


def test_render_page_pair_report_shows_counts_and_only_notable_lines():
    entries = [
        {"before_path": "pd.pdf", "before_page": 1, "after_path": "rd.pdf", "after_page": 1,
         "page_kind": "text", "level": 2, "status": "ok", "error": "",
         "findings": [{"label": "X", "change": "Y"}], "injection_suspected": False},
        {"before_path": "pd.pdf", "before_page": 2, "after_path": "rd.pdf", "after_page": 2,
         "page_kind": "text", "level": 2, "status": "ok", "error": "",
         "findings": [], "injection_suspected": False},
    ]
    report = registry_diff.render_page_pair_report(entries)
    assert "Пар проверено: 2" in report and "находок: 1" in report
    assert "X: Y" in report
    print("OK: отчёт по прямому сравнению листов считает пары и показывает находки")


if __name__ == "__main__":
    test_diff_splits_into_three_categories()
    test_diff_empty_sides_do_not_crash()
    test_load_text_facts_includes_catalog_pages_that_extract_document_facts_excludes()
    test_render_page_pair_report_shows_counts_and_only_notable_lines()
    print("ALL PASS (запустите pytest для тестов с capsys/monkeypatch/tmp_path)")
