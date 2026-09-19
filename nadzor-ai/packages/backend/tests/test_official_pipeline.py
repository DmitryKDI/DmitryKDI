"""Официальная сверка не подменяет нехватку источников готовым выводом."""

from types import SimpleNamespace

import pymupdf
from app import official_pipeline
from app.llm import LlmConfig


def document(doc_id: int, stage: str, path: str = "unused.pdf", **metadata):
    source_metadata = {
        "object_id": "synthetic-object", "stage": stage, "document_code": stage,
        "revision": "R", "approval_status": "APPROVED", "predecessor_id": None,
        **metadata,
    }
    return SimpleNamespace(
        id=doc_id, name=f"{stage}.pdf", file_path=path, pages=1,
        digest=f"digest-{doc_id}", source_metadata=source_metadata,
    )


def test_current_revision_requires_an_unambiguous_replacement_chain():
    first = document(1, "PD")
    second = document(2, "PD", predecessor_id=1)
    current, problem = official_pipeline._active_document([first, second])
    assert current is second
    assert problem is None

    competing = document(3, "PD", predecessor_id=1)
    current, problem = official_pipeline._active_document([first, second, competing])
    assert current is None
    assert "несколько редакций" in problem
    print("OK: актуальная редакция определяется только однозначной цепочкой замены")


def test_missing_key_is_reported_for_every_parameter_and_not_as_clean_result():
    result = official_pipeline.run_official_analysis(
        [document(1, "PD"), document(2, "RD")],
        LlmConfig(provider="gigachat", api_key="", base_url="", model=""),
    )
    assert result["coverage"] == {"total": 106, "completed": 0, "not_run": 106}
    assert all(item["completeness_status"] == "CLARIFICATION_REQUIRED"
               for item in result["checks"])
    assert all(item["finding_status"] is None and item["technical_status"] == "not_run"
               for item in result["checks"])
    print("OK: отсутствие ключа явно оставляет все параметры невыполненными")


def test_candidate_needs_verified_quotes_and_boxes_from_both_sides(tmp_path, monkeypatch):
    paths = {}
    # Встроенный базовый шрифт синтетического PDF гарантированно содержит
    # латиницу; смысл текста здесь не влияет на проверку привязки координат.
    texts = {"PD": "Object area is 120 square metres.",
             "RD": "Object area is 125 square metres."}
    documents = []
    for index, (stage, text) in enumerate(texts.items(), start=1):
        path = tmp_path / f"{stage}.pdf"
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), text)
            pdf.save(path)
        paths[str(path)] = text
        documents.append(document(index, stage, str(path)))

    parameter = {
        "code": "M-001", "name": "Площадь объекта", "priority": "HIGH",
        "unit": "м²", "source_pd": "ПД", "source_rd": "РД", "source_id": "ИД",
        "trigger": "значения различаются", "section": "Общие показатели",
    }
    monkeypatch.setattr(official_pipeline, "list_parameters", lambda: [parameter])
    monkeypatch.setattr(
        official_pipeline.facts_store, "facts_for",
        lambda path, _name, digest: SimpleNamespace(
            text_facts=[{"page": 1, "text": paths[path]}],
        ),
    )
    monkeypatch.setattr(official_pipeline, "call_llm_json", lambda *_args, **_kwargs: {
        "checks": [{
            "parameter_code": "M-001", "assessment": "CANDIDATE",
            "expected_value": "120", "actual_value": "125",
            "explanation": "Значения требуют проверки инспектором.",
            "evidence": [
                {"stage": "PD", "document_id": 1, "page": 1, "quote": texts["PD"]},
                {"stage": "RD", "document_id": 2, "page": 1, "quote": texts["RD"]},
            ],
        }],
    })

    result = official_pipeline.run_official_analysis(
        documents, LlmConfig(provider="gigachat", api_key="synthetic", base_url="", model=""),
        graphic_runner=None,
    )
    check = result["checks"][0]
    assert check["finding_status"] == "CANDIDATE"
    assert check["completeness_status"] == "COMPLETE"
    assert len(check["evidence"]) == 2
    assert all(item["bbox"] and all(0 <= value <= 1 for value in item["bbox"])
               for item in check["evidence"])
    print("OK: кандидат содержит проверенные цитаты и координаты обеих сторон")


def test_no_difference_without_sources_remains_incomplete(monkeypatch):
    parameter = {
        "code": "M-001", "name": "Параметр", "priority": "HIGH", "unit": "ед.",
        "source_pd": "ПД", "source_rd": "РД", "source_id": "ИД", "trigger": "сверка",
        "section": "Раздел",
    }
    monkeypatch.setattr(official_pipeline, "list_parameters", lambda: [parameter])
    monkeypatch.setattr(
        official_pipeline.facts_store, "facts_for",
        lambda *_args, **_kwargs: SimpleNamespace(text_facts=[]),
    )
    monkeypatch.setattr(official_pipeline, "call_llm_json", lambda *_args, **_kwargs: {
        "checks": [{"parameter_code": "M-001", "assessment": "NO_DIFFERENCE_OBSERVED",
                    "explanation": "Расхождение не замечено", "evidence": []}],
    })
    result = official_pipeline.run_official_analysis(
        [document(1, "PD"), document(2, "RD")],
        LlmConfig(provider="gigachat", api_key="synthetic", base_url="", model=""),
        graphic_runner=None,
    )
    check = result["checks"][0]
    assert check["completeness_status"] == "MISSING_EVIDENCE"
    assert check["finding_status"] is None
    assert "не является подтверждённым" in check["explanation"]
    print("OK: ответ без источников не становится подтверждённым отсутствием расхождения")


def test_graphic_analysis_is_separate_and_keeps_page_boxes(monkeypatch):
    parameter = {
        "code": "M-001", "name": "Параметр", "priority": "HIGH", "unit": "ед.",
        "source_pd": "ПД", "source_rd": "РД", "source_id": "ИД", "trigger": "сверка",
        "section": "Раздел",
    }
    monkeypatch.setattr(official_pipeline, "list_parameters", lambda: [parameter])
    monkeypatch.setattr(
        official_pipeline.facts_store, "facts_for",
        lambda *_args, **_kwargs: SimpleNamespace(text_facts=[]),
    )
    monkeypatch.setattr(official_pipeline, "call_llm_json", lambda *_args, **_kwargs: {
        "checks": [{"parameter_code": "M-001", "assessment": "INSUFFICIENT_EVIDENCE",
                    "explanation": "Текстовых данных недостаточно", "evidence": []}],
    })

    def graphic_runner(*_args, **_kwargs):
        return {
            "valid": True,
            "reason": "",
            "performance": {"duration_seconds": 1.5},
            "investigator": {"pages_inspected": 2},
            "semantic_findings": [{
                "difference_kind": "configuration",
                "detail": "На листах наблюдается различие конфигурации.",
                "pd_refs": ["PD0:P2"], "rd_refs": ["RD0:P3"],
                "pd_bbox": [0.1, 0.2, 0.4, 0.5], "rd_bbox": [0.2, 0.2, 0.5, 0.5],
                "pd_observation": "Проектное решение", "rd_observation": "Рабочее решение",
                "requirement_ids": ["R1"],
            }],
            "semantic_candidates": [],
        }

    result = official_pipeline.run_official_analysis(
        [document(1, "PD"), document(2, "RD")],
        LlmConfig(provider="gigachat", api_key="synthetic", base_url="", model=""),
        graphic_runner=graphic_runner,
    )
    graphic = result["graphic_analysis"]
    assert graphic["status"] == "completed"
    assert len(graphic["candidates"]) == 1
    candidate = graphic["candidates"][0]
    assert candidate["finding_status"] == "CANDIDATE"
    assert candidate["completeness_status"] == "COMPLETE"
    assert [(item["stage"], item["page"]) for item in candidate["evidence"]] == [
        ("PD", 2), ("RD", 3),
    ]
    print("OK: графический кандидат отделён от текста и хранит листы с координатами")
