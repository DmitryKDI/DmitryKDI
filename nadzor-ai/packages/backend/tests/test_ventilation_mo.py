import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ventilation_mo
from app.ventilation_mo import (
    MoFinding,
    cross_check_mo_branches,
    extract_branch_locations,
    extract_mo_table_page,
    is_mo_table_page,
    render_mo_cross_check_report,
)


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def test_is_mo_table_page_detects_real_title():
    """Дословный заголовок реального листа ПД (стр. 79-81 в разобранном
    комплекте) — единственная деterministic часть модуля, всё остальное
    читается только зрением (проверено: сама таблица и подписи веток РД
    не в текстовом слое)."""
    text_facts = [{"page": 79, "text": "Таблица воздухообменов помещений\n(начало)"}]
    assert is_mo_table_page(text_facts, 79) is True
    print("OK: реальный заголовок листа распознан текстом")


def test_is_mo_table_page_false_for_other_pages():
    text_facts = [{"page": 82, "text": "Таблица теплоизбытков помещений (начало)"}]
    assert is_mo_table_page(text_facts, 82) is False
    print("OK: другой заголовок листа не считается таблицей воздухообменов")


def test_extract_mo_table_page_parses_rooms():
    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        assert images and len(images) == 1
        return {"rooms": [
            {"room": "140", "name": "физического эксперимента", "supply_system": "П6",
             "exhaust_system": "ВЕ", "mo_branches": ["В2.7", "В2.8", "В2.9"], "mo_note": "950+950+950"},
        ]}
    orig = _patch(ventilation_mo, "call_llm_json", fake_call_llm_json)
    orig_render = _patch(ventilation_mo, "render_page_to_data_url", lambda *a, **kw: "data:image/png;base64,x")
    try:
        rooms = extract_mo_table_page("pd.pdf", 79, config=None)
    finally:
        ventilation_mo.call_llm_json = orig
        ventilation_mo.render_page_to_data_url = orig_render
    assert len(rooms) == 1
    assert rooms[0]["room"] == "140"
    assert rooms[0]["mo_branches"] == ["В2.7", "В2.8", "В2.9"]
    print("OK: таблица воздухообменов разбирается в список помещений с ветками М.О.")


def test_extract_mo_table_page_empty_when_response_unusable():
    orig = _patch(ventilation_mo, "call_llm_json", lambda *a, **kw: {"unrelated": True})
    orig_render = _patch(ventilation_mo, "render_page_to_data_url", lambda *a, **kw: "data:image/png;base64,x")
    try:
        rooms = extract_mo_table_page("pd.pdf", 79, config=None)
    finally:
        ventilation_mo.call_llm_json = orig
        ventilation_mo.render_page_to_data_url = orig_render
    assert rooms == []
    print("OK: неразбираемый ответ даёт пустой список, не падает")


def test_extract_branch_locations_parses_branches():
    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"branches": [
            {"branch": "В2.7", "nearest_room": "140", "system": "П2/ВЕ"},
            {"branch": "В2.10", "nearest_room": "140", "system": "П2/ВЕ"},
        ]}
    orig = _patch(ventilation_mo, "call_llm_json", fake_call_llm_json)
    orig_render = _patch(ventilation_mo, "render_page_to_data_url", lambda *a, **kw: "data:image/png;base64,x")
    try:
        branches = extract_branch_locations("rd.pdf", 18, config=None)
    finally:
        ventilation_mo.call_llm_json = orig
        ventilation_mo.render_page_to_data_url = orig_render
    assert len(branches) == 2
    assert branches[1]["branch"] == "В2.10"
    print("OK: план РД разбирается в список веток с ближайшим помещением")


def test_cross_check_detects_system_mismatch():
    """Реальный случай прогона: ПД задаёт систему «П6» для пом. 140, РД
    рисует «П2»."""
    pd_rooms = [{"room": "140", "supply_system": "П6", "mo_branches": ["В2.7"]}]
    rd_branches = [{"branch": "В2.7", "nearest_room": "140", "system": "П2/ВЕ"}]
    findings = cross_check_mo_branches(pd_rooms, rd_branches)
    types = [f.finding_type for f in findings]
    assert "system_mismatch" in types
    mismatch = next(f for f in findings if f.finding_type == "system_mismatch")
    assert "П6" in mismatch.detail and "П2" in mismatch.detail
    print("OK: несовпадение обозначения системы найдено")


def test_cross_check_detects_branch_relocated():
    """Реальный случай прогона: ветка В2.10 в ПД относится к пом. 147, на
    РД нарисована у пом. 140."""
    pd_rooms = [{"room": "147", "supply_system": "П6", "mo_branches": ["В2.10"]}]
    rd_branches = [{"branch": "В2.10", "nearest_room": "140", "system": "П6/ВЕ"}]
    findings = cross_check_mo_branches(pd_rooms, rd_branches)
    assert len(findings) == 1
    assert findings[0].finding_type == "branch_relocated"
    assert "147" in findings[0].detail and "140" in findings[0].detail
    print("OK: перенесённая на другое помещение ветка найдена")


def test_cross_check_detects_branch_missing():
    pd_rooms = [{"room": "142", "supply_system": "П6", "mo_branches": ["В2.5"]}]
    rd_branches = [{"branch": "В2.7", "nearest_room": "140", "system": "П2/ВЕ"}]
    findings = cross_check_mo_branches(pd_rooms, rd_branches)
    assert len(findings) == 1
    assert findings[0].finding_type == "branch_missing"
    assert "В2.5" in findings[0].detail
    print("OK: ветка, не найденная на РД вообще, помечена отдельно от перемещённой")


def test_cross_check_no_findings_when_everything_matches():
    pd_rooms = [{"room": "198", "supply_system": "П2", "mo_branches": ["В2.2", "В2.3"]}]
    rd_branches = [
        {"branch": "В2.2", "nearest_room": "198", "system": "П2/ВЕ"},
        {"branch": "В2.3", "nearest_room": "198", "system": "П2/ВЕ"},
    ]
    assert cross_check_mo_branches(pd_rooms, rd_branches) == []
    print("OK: полностью совпавшее помещение не даёт находок")


def test_cross_check_room_without_mo_branches_ignored():
    pd_rooms = [{"room": "314", "supply_system": "П3", "mo_branches": []}]
    assert cross_check_mo_branches(pd_rooms, []) == []
    print("OK: помещение без местных отсосов в ПД не даёт находок")


def test_render_mo_cross_check_report_lists_findings():
    findings = [MoFinding(room="140", finding_type="system_mismatch", detail="П6 -> П2")]
    report = render_mo_cross_check_report(findings)
    assert "находок: 1" in report
    assert "140" in report and "П6 -> П2" in report
    print("OK: отчёт перечисляет находки с номером помещения и деталями")


if __name__ == "__main__":
    test_is_mo_table_page_detects_real_title()
    test_is_mo_table_page_false_for_other_pages()
    test_extract_mo_table_page_parses_rooms()
    test_extract_mo_table_page_empty_when_response_unusable()
    test_extract_branch_locations_parses_branches()
    test_cross_check_detects_system_mismatch()
    test_cross_check_detects_branch_relocated()
    test_cross_check_detects_branch_missing()
    test_cross_check_no_findings_when_everything_matches()
    test_cross_check_room_without_mo_branches_ignored()
    test_render_mo_cross_check_report_lists_findings()
    print("ALL PASS")
