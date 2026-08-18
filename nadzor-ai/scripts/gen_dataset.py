#!/usr/bin/env python3
"""Генерация демонстрационного комплекта документации и эталона ground_truth.

Запуск:  python scripts/gen_dataset.py [--out data/demo/generated]

Комплект строится до кода детектора и определяет схемы извлечения, промпты и
метрики. Реальная документация не используется: все организации и лица вымышлены.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.dataset import documents as blocks
from scripts.dataset.fixtures import OBJECTS, PD_REV2, PD_REV3, STAFF
from scripts.dataset.kits import (
    AOSR_OBJ001,
    AOSR_OBJ002,
    AOSR_OBJ003,
    PASSPORTS_OBJ001,
    TEST_REPORTS_OBJ001,
)
from scripts.dataset.render import build_pdf

CHANGES_REV3 = [
    ["3", "12, 14", "Класс бетона плиты перекрытия ПП-8 на отм. +33.600 изменён с B30 на B25",
     "Протокол ТС от 21.06.2024", "26.06.2024"],
    ["3", "12", "Толщина плиты перекрытия ПП-8 изменена с 200 мм на 180 мм",
     "Протокол ТС от 21.06.2024", "26.06.2024"],
    ["3", "12", "Класс рабочей арматуры колонн К-2 изменён с A500C на A400",
     "Письмо подрядчика № 418 от 17.06.2024", "26.06.2024"],
    ["3", "8", "Марка бетона фундаментной плиты ФП-1 по морозостойкости изменена с F150 на F100",
     "Протокол ТС от 21.06.2024", "26.06.2024"],
    ["3", "8", "Отметка низа фундаментной плиты ФП-1 изменена с -4.200 на -4.500",
     "Уточнение инженерно-геологических изысканий", "26.06.2024"],
    ["3", "16", "Добавлена монолитная лестница Л-3 в осях В–Г / 4–5",
     "Задание застройщика от 03.06.2024", "26.06.2024"],
]

# Рабочая документация объекта OBJ-001: наследует редакцию 3 проектной документации.
RD_ROWS = [r[:] for r in PD_REV3]


def _doc(manifest: list, obj_id: str, path: Path, kind: str, state: str, title: str,
         revision: str = "1", date: str = "", section: str = "") -> None:
    manifest.append({
        "object_id": obj_id, "file": str(path).replace("\\", "/"), "doc_kind": kind,
        "state_kind": state, "title": title, "revision": revision, "date": date,
        "section": section,
    })


def build_object_1(out: Path, manifest: list) -> None:
    obj = OBJECTS[0]
    d = out / obj["id"]

    p = build_pdf(d / "permit" / "razreshenie.pdf", blocks.permit_blocks(obj))
    _doc(manifest, obj["id"], p, "permit", "PERMIT", "Разрешение на строительство",
         date=obj["permit_date"])

    p = build_pdf(d / "pd_rev2" / "2024-15-KR_izm2.pdf",
                  blocks.pd_blocks(obj, PD_REV2, "2", "2024-03-12", None),
                  blocks.stamp_for(obj, "2024-15-КР", "Конструктивные решения", "П", "2", "2024-03-12"),
                  wide=True)
    _doc(manifest, obj["id"], p, "pd", "PD", "Проектная документация, раздел КР, редакция 2",
         "2", "2024-03-12", "КР")

    p = build_pdf(d / "pd_rev3" / "2024-15-KR_izm3.pdf",
                  blocks.pd_blocks(obj, PD_REV3, "3", "2024-06-26", CHANGES_REV3),
                  blocks.stamp_for(obj, "2024-15-КР", "Конструктивные решения", "П", "3", "2024-06-26"),
                  wide=True)
    _doc(manifest, obj["id"], p, "pd", "PD", "Проектная документация, раздел КР, редакция 3",
         "3", "2024-06-26", "КР")

    p = build_pdf(d / "expertise" / "zaklyuchenie_expertizy.pdf", blocks.expertise_blocks(obj), wide=True)
    _doc(manifest, obj["id"], p, "expertise", "EXPERTISE",
         "Заключение государственной экспертизы", "1", obj["expertise"]["date"])

    p = build_pdf(d / "rd" / "2024-15-KZH_izm1.pdf", blocks.rd_blocks(obj, RD_ROWS, "2024-07-05", rebar_step="250"),
                  blocks.stamp_for(obj, "2024-15-КЖ", "Конструкции железобетонные", "Р", "1", "2024-07-05"),
                  wide=True)
    _doc(manifest, obj["id"], p, "rd", "RD", "Рабочая документация, марка КЖ", "1", "2024-07-05", "КЖ")

    for act in AOSR_OBJ001:
        p = build_pdf(d / "as_built" / f"aosr_{act['n']}.pdf", blocks.aosr_blocks(obj, act))
        _doc(manifest, obj["id"], p, "aosr", "AS_BUILT",
             f"Акт освидетельствования скрытых работ № {act['n']}", "1", act["date"])

    p = build_pdf(d / "as_built" / "obshchiy_zhurnal_rabot.pdf", blocks.journal_blocks(obj, AOSR_OBJ001),
                  wide=True)
    _doc(manifest, obj["id"], p, "journal", "AS_BUILT", "Общий журнал работ", "1", "2024-09-16")

    for ps in PASSPORTS_OBJ001:
        p = build_pdf(d / "as_built" / f"passport_{ps['n']}.pdf", blocks.passport_blocks(obj, ps))
        _doc(manifest, obj["id"], p, "passport", "AS_BUILT",
             f"Паспорт качества {ps['n']}", "1", ps["date"])

    for r in TEST_REPORTS_OBJ001:
        p = build_pdf(d / "as_built" / f"protokol_{r['n']}.pdf", blocks.test_report_blocks(obj, r))
        _doc(manifest, obj["id"], p, "test_report", "AS_BUILT",
             f"Протокол испытаний {r['n']}", "1", r["date"])

    p = build_pdf(d / "as_built" / "vypiska_sro.pdf", blocks.sro_blocks(obj))
    _doc(manifest, obj["id"], p, "sro_extract", "AS_BUILT",
         "Выписка из реестра членов СРО", "1", "2024-01-15")


def build_small_object(out: Path, manifest: list, obj: dict, acts: list, pd_date: str,
                       rd_date: str) -> None:
    """Сокращённый комплект: разрешение, ПД, РД, акты, журнал."""
    d = out / obj["id"]
    p = build_pdf(d / "permit" / "razreshenie.pdf", blocks.permit_blocks(obj))
    _doc(manifest, obj["id"], p, "permit", "PERMIT", "Разрешение на строительство",
         date=obj["permit_date"])

    p = build_pdf(d / "pd_rev1" / "pd_kr.pdf", blocks.pd_blocks(obj, PD_REV2[:4], "1", pd_date, None),
                  blocks.stamp_for(obj, f"{obj['id']}-КР", "Конструктивные решения", "П", "1", pd_date),
                  wide=True)
    _doc(manifest, obj["id"], p, "pd", "PD", "Проектная документация, раздел КР", "1", pd_date, "КР")

    p = build_pdf(d / "rd" / "rd_kzh.pdf", blocks.rd_blocks(obj, PD_REV2[:4], rd_date),
                  blocks.stamp_for(obj, f"{obj['id']}-КЖ", "Конструкции железобетонные", "Р", "1", rd_date),
                  wide=True)
    _doc(manifest, obj["id"], p, "rd", "RD", "Рабочая документация, марка КЖ", "1", rd_date, "КЖ")

    for act in acts:
        p = build_pdf(d / "as_built" / f"aosr_{act['n']}.pdf", blocks.aosr_blocks(obj, act))
        _doc(manifest, obj["id"], p, "aosr", "AS_BUILT",
             f"Акт освидетельствования скрытых работ № {act['n']}", "1", act["date"])

    p = build_pdf(d / "as_built" / "obshchiy_zhurnal_rabot.pdf", blocks.journal_blocks(obj, acts), wide=True)
    _doc(manifest, obj["id"], p, "journal", "AS_BUILT", "Общий журнал работ", "1", acts[-1]["date"])

    p = build_pdf(d / "as_built" / "vypiska_sro.pdf", blocks.sro_blocks(obj))
    _doc(manifest, obj["id"], p, "sro_extract", "AS_BUILT", "Выписка из реестра членов СРО",
         "1", "2024-01-20")


def main() -> None:
    parser = argparse.ArgumentParser(description="Генерация демо-комплекта документации")
    parser.add_argument("--out", default="data/demo/generated")
    args = parser.parse_args()
    out = Path(args.out)

    manifest: list = []
    build_object_1(out, manifest)
    build_small_object(out, manifest, OBJECTS[1], AOSR_OBJ002, "2024-04-02", "2024-06-11")
    build_small_object(out, manifest, OBJECTS[2], AOSR_OBJ003, "2023-08-21", "2023-12-04")

    (out / "manifest.json").write_text(
        json.dumps({"objects": OBJECTS, "staff": STAFF, "documents": manifest},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Сформировано документов: {len(manifest)}")
    for obj in OBJECTS:
        n = sum(1 for m in manifest if m["object_id"] == obj["id"])
        print(f"  {obj['id']}  {n:>3} док.  {obj['name'][:60]}")
    print(f"Манифест: {out / 'manifest.json'}")


if __name__ == "__main__":
    main()
