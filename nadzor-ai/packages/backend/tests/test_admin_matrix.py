"""Матрица и нормативная база в базе данных, правка без перекодирования (ТЗ 8.1, модуль 8)."""
from app import models, parameter_catalog
from app.db import SessionLocal
from app.main import app
from app.official_pipeline import threshold_status
from fastapi.testclient import TestClient

client = TestClient(app)


def test_params_table_is_seeded_with_all_fields_of_section_8_1():
    parameter_catalog.ensure_seeded()
    with SessionLocal() as db:
        assert db.query(models.Param).count() == parameter_catalog.PARAMETER_COUNT
    item = client.get("/api/v1/admin/params").json()["parameters"][0]
    for field in ("code", "section", "parameter_name", "description", "unit", "source_pd",
                  "source_rd", "source_id", "trigger_logic", "review_priority",
                  "sp_reference", "gost_reference", "fz_reference", "other_normative",
                  "data_type", "min_value", "max_value", "regex_pattern", "is_active",
                  "created_at", "updated_at"):
        assert field in item, field
    print("OK: таблица Params заполнена официальной матрицей со всеми полями п. 8.1")


def test_admin_changes_threshold_and_matrix_revision_grows():
    before = parameter_catalog.current_matrix_version()
    response = client.patch("/api/v1/admin/params/M-001",
                            json={"min_value": 1, "max_value": 5, "sp_reference": "СП X"})
    assert response.status_code == 200, response.text
    assert response.json()["min_value"] == 1 and response.json()["max_value"] == 5
    assert parameter_catalog.current_matrix_version() != before
    assert parameter_catalog.get_parameter("M-001")["sp_reference"] == "СП X"
    wrong = client.patch("/api/v1/admin/params/M-001", json={"min_value": 9})
    assert wrong.status_code == 422, "минимум больше максимума принят"
    bad_regex = client.patch("/api/v1/admin/params/M-001", json={"regex_pattern": "("})
    assert bad_regex.status_code == 422
    client.patch("/api/v1/admin/params/M-001",
                 json={"clear_min_value": True, "clear_max_value": True, "sp_reference": ""})
    assert parameter_catalog.get_parameter("M-001")["min_value"] is None
    print("OK: администратор меняет пороги и ссылки; каждая правка — новая ревизия матрицы")


def test_inactive_parameter_is_not_checked():
    client.patch("/api/v1/admin/params/M-002", json={"is_active": False})
    try:
        codes = [item["code"] for item in parameter_catalog.list_parameters()]
        assert "M-002" not in codes
        assert len(codes) == parameter_catalog.PARAMETER_COUNT - 1
    finally:
        client.patch("/api/v1/admin/params/M-002", json={"is_active": True})
    assert len(parameter_catalog.list_parameters()) == parameter_catalog.PARAMETER_COUNT
    print("OK: деактивированный параметр в проверку не идёт")


def test_threshold_is_checked_by_code_not_by_model():
    parameter = {"min_value": 2.5, "max_value": None}
    assert threshold_status(parameter, "2,4 м") == "OUT_OF_RANGE"
    assert threshold_status(parameter, "2.7") == "WITHIN"
    assert threshold_status(parameter, "не указано") is None
    assert threshold_status({}, "3") is None
    print("OK: выход за порог Params определяет код")


def test_normative_base_and_rules_are_validated(act_as):
    norm = client.post("/api/v1/admin/normative", json={
        "document_name": "Свод правил", "document_number": "СП 0.0", "section": "п. 1",
        "parameter_name": "M-003", "min_value": 1, "max_value": 2})
    assert norm.status_code == 200, norm.text
    unknown = client.post("/api/v1/admin/normative", json={
        "document_name": "Свод правил", "document_number": "СП 0.0",
        "parameter_name": "M-999"})
    assert unknown.status_code == 422
    client.put(f"/api/v1/admin/normative/{norm.json()['id']}", json={
        **norm.json(), "is_active": False})
    rule = client.post("/api/v1/admin/rules", json={
        "rule_name": "Если A, то B", "condition": "M-001 > 10", "expected": "present(M-002)"})
    assert rule.status_code == 200, rule.text
    client.put(f"/api/v1/admin/rules/{rule.json()['id']}",
               json={**rule.json(), "is_active": False})
    broken = client.post("/api/v1/admin/rules", json={
        "rule_name": "x", "condition": "M-001 >", "expected": "present(M-002)"})
    assert broken.status_code == 422
    act_as("inspector")
    assert client.post("/api/v1/admin/rules", json={
        "rule_name": "x", "condition": "present(M-001)",
        "expected": "present(M-002)"}).status_code == 403
    assert client.get("/api/v1/admin/rules").status_code == 200
    print("OK: нормы и правила заводит администратор; ошибка в выражении отклоняется")


def test_thresholds_are_parsed_from_trigger_text():
    from app import trigger_thresholds as t

    assert t.parse("Дельта общей площади между ПД и РД (или ИД) > 1%.") == {"max_delta_pct": 1.0}
    assert t.parse("Ширина пожарного проезда в РД менее 4.2 м.") == {"min_value": 4.2,
                                                                     "unit": "м"}
    assert t.parse("Сужение временных дорог < 3.5-4.5 м.")["min_value"] == 3.5
    assert t.parse("Высота порога > 0.014 м (СП 00.00000).") == {"max_value": 0.014,
                                                                  "unit": "м"}
    assert t.parse("Смещение точки подключения относительно ПД > 0.5 м.")["max_abs_delta"] == 0.5
    # Номер нормы — не порог; доля от целого — не расхождение стадий;
    # два порога для разных элементов — не разбираются.
    assert t.parse("Изменение диаметров (СП 60.13330).") == {}
    assert t.parse("Уменьшение доли мест (< 10% от общего числа).") == {}
    assert t.parse("Высота коридоров < 2.0 м или дверей < 1.9 м.") == {}
    print("OK: пороги извлекаются из текста правила только однозначные")


def test_trigger_thresholds_are_checked_by_code_with_units():
    corridor = {"trigger": "Снижение ширины коридора менее 1.2 м."}
    assert threshold_status(corridor, "1150 мм") == "OUT_OF_RANGE"
    assert threshold_status(corridor, "1,4 м") == "WITHIN"
    area = {"trigger": "Дельта общей площади между ПД и РД > 1%."}
    assert threshold_status(area, "1015 м²", "1000 м²") == "DELTA_EXCEEDED"
    assert threshold_status(area, "1005 м²", "1000 м²") == "WITHIN"
    admin = {"trigger": "Ширина менее 1.2 м.", "min_value": 1.0}
    assert threshold_status(admin, "1.1 м") == "WITHIN", "порог администратора приоритетнее"
    print("OK: выход за порог и расхождение стадий проверяются кодом, с учётом единиц")
