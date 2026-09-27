"""Пример выходного JSON процесса проверки — настоящим кодом конвейера.

Документы синтетические, ответ модели задан сценарием: пример показывает
ФОРМУ результата (статусы, полнота, доказательства с листом и координатами),
а не качество модели. Реквизитов реальных объектов здесь нет и быть не может.

    .venv/bin/python scripts/make_example_output.py > docs/example-output.json
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app import official_pipeline  # noqa: E402
from app.llm import LlmConfig  # noqa: E402
from app.parameter_catalog import list_parameters  # noqa: E402

TEXTS = {"PD": "Total area of the building: 1200 m2.",
         "RD": "Total area of the building: 1250 m2."}


def _documents(folder: Path) -> list[SimpleNamespace]:
    documents = []
    for index, (stage, text) in enumerate(TEXTS.items(), start=1):
        path = folder / f"{stage}.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), text)
            pdf.save(path)
        documents.append(SimpleNamespace(
            id=index, name=f"{stage}-synthetic.pdf", file_path=str(path), pages=1,
            digest=f"synthetic-{index}",
            source_metadata={"object_id": "synthetic-object", "stage": stage,
                             "document_code": f"XXX/000000/1-{stage}", "revision": "1",
                             "approval_status": "APPROVED", "approval_date": "2026-01-01",
                             "predecessor_id": None}))
    return documents


def main() -> None:
    parameter = list_parameters()[0]
    official_pipeline.list_parameters = lambda: [parameter]
    official_pipeline.facts_store.facts_for = lambda path, _name, digest: SimpleNamespace(
        text_facts=[{"page": 1, "text": TEXTS[Path(path).stem]}])
    official_pipeline.call_llm_json = lambda *_a, **_kw: {"checks": [{
        "parameter_code": parameter["code"], "assessment": "CANDIDATE",
        "expected_value": "1200", "actual_value": "1250",
        "explanation": "Значение в РД отличается от ПД; требуется проверка инспектором.",
        "evidence": [
            {"stage": "PD", "document_id": 1, "page": 1, "quote": TEXTS["PD"]},
            {"stage": "RD", "document_id": 2, "page": 1, "quote": TEXTS["RD"]},
        ],
    }]}
    with tempfile.TemporaryDirectory() as folder:
        result = official_pipeline.run_official_analysis(
            _documents(Path(folder)), LlmConfig(), graphic_runner=None)
    envelope = {"process_id": 1, "status": "completed", "stage": "Готово",
                "completed": result["coverage"]["total"], "total": result["coverage"]["total"],
                "error": None, "result": result}
    json.dump(envelope, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
