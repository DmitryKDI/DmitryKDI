"""Generate benchmark-safe focus-v3 synthetic PD/RD cases.

The cases target generic ventilation reasoning: supply-unit configuration changes,
room-by-room exhaust coverage, topology changes, and hard negatives. All entity
ids and room numbers are fictional and unrelated to the real benchmark.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_synthetic_corpus import LESSONS, ROOT, _pdf

DEFAULT_FOCUS_V3_OUT = ROOT / "data" / "synthetic_training" / "focus_v3"

FOCUS_V3_CASES = [
    (43,"configuration","Изменение состава приточной установки","721","AHU-Y43","VENT","Приточная установка AHU-Y43: DAMPER -> FILTER-G4 -> HEATER -> FAN -> SILENCER.","Приточная установка AHU-Y43: DAMPER -> FILTER-G4 -> FAN -> SILENCER; секция HEATER отсутствует.","Из состава приточной установки удалена обязательная функциональная секция."),
    (44,"configuration","Изменение схемы вентиляторов приточной установки","722","AHU-Y44","VENT","AHU-Y44 выполнить с двумя параллельными рабочими вентиляторами FAN-A и FAN-B после фильтра F7.","AHU-Y44 выполнена с одним вентилятором FAN-A после фильтра F7; FAN-B не предусмотрен.","Изменена конфигурация вентиляторной секции приточной установки."),
    (45,"configuration","Перенос секции нагрева относительно вентилятора","723","AHU-Y45","VENT","AHU-Y45: FILTER -> HEATER -> FAN -> SILENCER.","AHU-Y45: FILTER -> FAN -> HEATER -> SILENCER.","Изменена последовательность функциональных секций установки."),
    (46,"connection","Переподключение приточной установки к другой магистрали","724","AHU-Y46","VENT","Выход AHU-Y46 подключить к магистрали SUP-Y46A, обслуживающей зону 724.","Выход AHU-Y46 подключен к магистрали SUP-Y46B, обслуживающей соседнюю зону.","Изменена целевая магистраль приточной установки."),
    (47,"single_room","Пропуск вытяжки в части помещений","725","EX-Y47","VENT","Отдельную вытяжку EX-Y47 предусмотреть в помещениях 725, 726, 727 и 728.","Отдельная вытяжка EX-Y47 предусмотрена в помещениях 725 и 728; для 726 и 727 отдельные вытяжные точки не указаны.","Часть обязательных помещений потеряла требуемую вытяжку."),
    (48,"connection","Объединение вытяжек разных помещений","729","EX-Y48A","VENT","Помещение 729 обслуживать системой EX-Y48A, помещение 730 — отдельной системой EX-Y48B; системы не объединять.","Вытяжки помещений 729 и 730 объединены общей магистралью EX-Y48A и подключены к одному вентилятору.","Две независимые вытяжные системы объединены в одну."),
    (49,"absence","Удаление вытяжной точки при сохранении общеобменной системы","731","LE-Y49","VENT","В помещении 731 предусмотреть местную вытяжную точку LE-Y49 дополнительно к общеобменной вытяжке.","В помещении 731 предусмотрена только общеобменная вытяжка; местная точка LE-Y49 не показана.","Обязательная локальная вытяжная точка отсутствует при сохранении общей системы."),
    (50,"cross_sheet","Расхождение вытяжки между планом и схемой","732","EX-Y50","VENT","Для помещения 732 предусмотреть отдельную вытяжку EX-Y50 с подключением к EF-Y50.","На плане помещения 732 обозначена EX-Y50, но на принципиальной схеме ветка помещения 732 подключена к общей EX-Y51 и EF-Y50 отсутствует.","План и схема РД расходятся по топологии вытяжки относительно ПД."),
    (51,"negative_control","Эквивалентная запись состава приточной установки","733","NEG-Y51","NEG","NEG-Y51: фильтр F7, затем водяной нагреватель, затем вентилятор.","NEG-Y51: F7 filtration -> water heating -> fan.",""),
    (52,"negative_control","Эквивалентное описание двух вентиляторов","734","NEG-Y52","NEG","Установка NEG-Y52 имеет два параллельных рабочих вентилятора.","В NEG-Y52 предусмотрено 2 duty fan, работающих параллельно.",""),
    (53,"negative_control","Эквивалентное покрытие помещений вытяжкой","735","NEG-Y53","NEG","Систему NEG-Y53 предусмотреть в помещениях 735, 736 и 737.","NEG-Y53 обслуживает помещения 737, 735 и 736.",""),
    (54,"negative_control","Эквивалентное описание подключения вытяжки","738","NEG-Y54","NEG","Вытяжку NEG-Y54 помещения 738 подключить через клапан DMP-Y54 к вентилятору EF-Y54.","Путь воздуха из помещения 738: NEG-Y54 -> DMP-Y54 -> EF-Y54.",""),
]


def generate_focus_v3(out: Path = DEFAULT_FOCUS_V3_OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "3.0-focus",
        "benchmark_safe": True,
        "purpose": "supply_unit_configuration_multiroom_exhaust_hard_negatives",
        "cases": [],
    }
    for case in FOCUS_V3_CASES:
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
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate focus-v3 synthetic curriculum")
    parser.add_argument("--out", type=Path, default=DEFAULT_FOCUS_V3_OUT)
    args = parser.parse_args()
    manifest = generate_focus_v3(args.out)
    positives = sum(bool(row["positive"]) for row in manifest["cases"])
    negatives = len(manifest["cases"]) - positives
    print(f"Generated {len(manifest['cases'])} focus-v3 cases ({positives} positive, {negatives} hard negative) in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
