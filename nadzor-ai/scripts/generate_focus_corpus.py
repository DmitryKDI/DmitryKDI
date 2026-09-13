"""Generate a small second-stage synthetic curriculum for ventilation reasoning.

This corpus is benchmark-safe: all room numbers, system marks, equipment names
and expected answers are fictional and independent from the real mini benchmark.
It is intended to continue training from already persisted generic memory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_synthetic_corpus import LESSONS, ROOT, _pdf

DEFAULT_FOCUS_OUT = ROOT / "data" / "synthetic_training" / "focus_v2"

FOCUS_CASES = [
    (31,"configuration","Перестановка секций приточной установки","701","AHU-X31","VENT","AHU-X31: FILTER-G4 -> HEATER -> COOLER -> FAN -> SILENCER.","AHU-X31: FILTER-G4 -> HEATER -> FAN -> COOLER -> SILENCER.","Изменен порядок функциональных секций приточной установки."),
    (32,"configuration","Изменение рециркуляционного контура","702","AHU-X32","VENT","Рециркуляционный воздух AHU-X32 подавать перед секцией фильтрации FILTER-F7.","Рециркуляционный воздух AHU-X32 подключен после секции FILTER-F7 непосредственно перед вентилятором.","Изменена точка подключения рециркуляционного контура."),
    (33,"connection","Объединение независимых вытяжных веток","703","EX-X33A","VENT","Вытяжку EX-X33A из помещения 703 подключить к EF-X33A. Вытяжку EX-X33B из помещения 803 подключить отдельно к EF-X33B. Ветки не объединять.","Вытяжки EX-X33A и EX-X33B объединены общей магистралью и подключены к EF-X33A.","Независимые вытяжные ветки объединены и одна целевая связь потеряна."),
    (34,"connection","Переподключение местной вытяжки","704","LE-X34","VENT","Местную вытяжку LE-X34 помещения 704 подключить к вентилятору EF-X34A.","Местная вытяжка LE-X34 помещения 704 подключена к вентилятору EF-X34B.","Изменен целевой вентилятор местной вытяжки."),
    (35,"single_room","Пропуск вытяжки в одном помещении","705","EX-X35","VENT","Систему EX-X35 предусмотреть отдельно в помещениях 705, 706 и 707.","Система EX-X35 предусмотрена в помещениях 705 и 707. Для помещения 706 отдельная вытяжка не указана.","Одно из нескольких обязательных помещений осталось без требуемой вытяжки."),
    (36,"absence","Удаление местного отсоса","708","LE-X36","VENT","Над технологической точкой в помещении 708 предусмотреть местный отсос LE-X36 с самостоятельным подключением к вытяжной сети.","В помещении 708 предусмотрена только общеобменная вытяжка; местный отсос LE-X36 не предусмотрен.","Обязательный местный отсос заменен только общеобменной вытяжкой."),
    (37,"cross_sheet","Разная марка клапана на плане и в спецификации","709","VAV-X37","VENT","Для помещения 709 принять клапан VAV-X37-A; эта же марка должна быть отражена в спецификации.","На плане указан VAV-X37-A, а в спецификации указан VAV-X37-B.","План и спецификация РД расходятся по марке клапана относительно ПД."),
    (38,"configuration","Сокращение ступеней очистки воздуха","710","AHU-X38","VENT","AHU-X38 должна иметь две последовательные ступени очистки: PRE-FILTER-G4 -> FILTER-F9.","AHU-X38 содержит только FILTER-F9; предварительная ступень G4 исключена.","Из состава приточной установки удалена обязательная ступень очистки."),
    (39,"negative_control","Эквивалентное описание порядка секций","711","NEG-X39","NEG","NEG-X39: сначала фильтр F7, затем нагреватель, затем вентилятор.","NEG-X39 выполнена последовательностью: фильтрация F7 -> нагрев -> вентилятор.",""),
    (40,"negative_control","Эквивалентное описание локальной вытяжки","712","NEG-X40","NEG","В помещениях 712 и 812 предусмотреть по одной локальной вытяжке NEG-X40 с подключением к FAN-X40.","Локальные вытяжки NEG-X40 установлены по одной в помещениях 712 и 812; обе подключены к FAN-X40.",""),
    (41,"negative_control","Эквивалентное описание резервирования","713","NEG-X41","NEG","Группа NEG-X41: два рабочих вентилятора и один резервный.","NEG-X41 состоит из трех вентиляторов: 2 duty и 1 standby.",""),
    (42,"negative_control","Эквивалентное описание подключения ветки","714","NEG-X42","NEG","Ветку NEG-X42 из помещения 714 подключить к вентилятору FAN-X42 через клапан DMP-X42.","Из помещения 714 ветка NEG-X42 через DMP-X42 приходит на FAN-X42.",""),
]


def generate_focus(out: Path = DEFAULT_FOCUS_OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "2.0-focus",
        "benchmark_safe": True,
        "purpose": "configuration_connection_local_exhaust_hard_negatives",
        "cases": [],
    }
    for case in FOCUS_CASES:
        idx, category, title, room, mark, system, pd_text, rd_text, fact = case
        cid = f"SYN-{idx:03d}"
        cdir = out / cid
        cdir.mkdir(parents=True, exist_ok=True)
        pd_path = cdir / f"{cid}_PD.pdf"
        rd_path = cdir / f"{cid}_RD.pdf"
        _pdf(pd_path, case, rd=False)
        _pdf(rd_path, case, rd=True)

        positive = category != "negative_control"
        expected = None if not positive else {
            "type": category,
            "room": room,
            "mark": mark,
            "system": system,
            "pd_page": 1,
            "rd_page": 1,
            "fact": fact,
        }
        gt = {
            "case_id": cid,
            "category": category,
            "positive": positive,
            "expected": expected,
            "allowed_training_abstraction": {
                "category": category,
                "lesson": LESSONS[category],
            },
        }
        gt_path = cdir / "ground_truth.json"
        gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["cases"].append({
            "case_id": cid,
            "category": category,
            "positive": positive,
            "pd": str(pd_path.relative_to(out)),
            "rd": str(rd_path.relative_to(out)),
            "ground_truth": str(gt_path.relative_to(out)),
        })

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate focused second-stage synthetic curriculum")
    parser.add_argument("--out", type=Path, default=DEFAULT_FOCUS_OUT)
    args = parser.parse_args()
    manifest = generate_focus(args.out)
    positives = sum(bool(row["positive"]) for row in manifest["cases"])
    negatives = len(manifest["cases"]) - positives
    print(f"Generated {len(manifest['cases'])} focus cases ({positives} positive, {negatives} hard negative) in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
