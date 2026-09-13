"""Generate a benchmark-safe synthetic PD->RD curriculum.

The corpus intentionally uses fictional room numbers, system marks and equipment
names that do not occur in the real mini benchmark. Ground truth is used only for
offline scoring; runtime prompts must never consume expected answers.
"""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "synthetic_training" / "generated"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/calibri.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)

CASES = [
    (1,"deletion","Удаление вытяжной решетки","501","EX-501","V-21","В помещении 501 предусмотреть отдельную вытяжную решетку EX-501, подключенную к системе V-21. Решетка обязательна для местного удаления воздуха.","В помещении 501 выполнена общеобменная вентиляция системой V-21. Отдельная вытяжная решетка EX-501 в составе листа не указана.","В РД удалена предусмотренная ПД вытяжная решетка EX-501."),
    (2,"quantity","Изменение количества диффузоров","503","SUP-A","SUP","В помещении 503 предусмотреть три потолочных диффузора SUP-A, равномерно распределенных по зоне.","В помещении 503 предусмотрены два потолочных диффузора SUP-A.","Количество диффузоров изменено с 3 до 2."),
    (3,"type_change","Замена типа радиатора","505","RAD-53","HEAT","Основной отопительный прибор в помещении 505: стальной панельный радиатор ALPHA-P22-500-1000.","Основной отопительный прибор в помещении 505: стальной панельный радиатор BETA-X2-500-1000.","Марка отопительного прибора изменена."),
    (4,"parameter","Изменение диаметра трубопровода","507","H-31","H-31","Подающий трубопровод H-31 к помещению 507 выполнить DN25.","Подающий трубопровод H-31 к помещению 507 выполнить DN32.","Диаметр DN25 изменен на DN32."),
    (5,"parameter","Изменение температурного графика","509","H-40","H-40","Расчетный температурный график контура H-40: 90/70 C.","Расчетный температурный график контура H-40: 80/60 C.","Температурный график изменен 90/70 -> 80/60."),
    (6,"location","Перенос воздухозабора","511","SUP-41","SUP-41","Воздухозабор системы SUP-41 разместить на кровле, отметка +12.000.","Воздухозабор системы SUP-41 размещен на северном фасаде, отметка +3.600.","Место воздухозабора изменено с кровли на фасад."),
    (7,"connection","Переподключение вытяжной ветки","513","EX-51","EX-51","Вытяжную ветку из помещения 513 подключить к вентилятору EF-A.","Вытяжная ветка из помещения 513 подключена к вентилятору EF-B.","Ветка переподключена с EF-A на EF-B."),
    (8,"configuration","Изменение последовательности секций ПВУ","515","AHU-51","AHU-51","Установка AHU-51: FILTER-F7 -> HEATER -> FAN -> SILENCER.","Установка AHU-51: FILTER-F7 -> FAN -> HEATER -> SILENCER.","Изменена последовательность секций установки."),
    (9,"single_room","Локальный пропуск балансировочного клапана","518","BV-61","H-61","На ответвлениях H-61 в помещениях 517, 518 и 519 установить балансировочный клапан BV-61.","Балансировочные клапаны BV-61 установлены в помещениях 517 и 519.","Из трех обязательных помещений BV-61 пропущен только в 518."),
    (10,"cross_sheet","Объединение независимых вытяжных систем","520","EX-K1","EX-K1/EX-S1","Помещение 520 обслуживать отдельной вытяжной системой EX-K1. Помещение 521 обслуживать отдельной системой EX-S1. Системы не объединять.","На плане помещения 520 и 521 подключены к общей магистрали EX-K1.","Две независимые вытяжки объединены в одну."),
    (11,"absence","Удаление противопожарного клапана","522","FD-71","FD-71","FD-71 обязателен на границе помещения 522.","На границе помещения 522 FD-71 не показан.","Удален противопожарный клапан."),
    (12,"control","Замена датчика управления","523","TS-72","CTRL","Управление AHU выполнять по датчику TS-72-T (температура).","Управление AHU выполняется по TS-72-P (давление).","Изменен тип управляющего датчика."),
    (13,"quantity","Снижение резервирования насосов","524","P-73","PUMP","Насосная группа: 2 рабочих + 1 резервный насос.","Насосная группа: 1 рабочий + 1 резервный насос.","Снижено резервирование насосов."),
    (14,"absence","Удаление регулятора перепада","525","DPV-74","HYD","На ветке DPV-74 установить регулятор перепада давления.","На ветке DPV-74 регулятор перепада не предусмотрен.","Удален регулятор перепада давления."),
    (15,"parameter","Уменьшение толщины изоляции","526","INS-75","INS","Изоляция INS-75: 40 мм.","Изоляция INS-75: 20 мм.","Толщина изоляции уменьшена."),
    (16,"parameter","Изменение сечения воздуховода","527","DUCT-76","VENT","Воздуховод DUCT-76: 500x300 мм.","Воздуховод DUCT-76: 400x250 мм.","Сечение воздуховода уменьшено."),
    (17,"quantity","Уменьшение числа вентиляторов","528","FAN-77","VENT","Система FAN-77: два параллельных вентилятора.","Система FAN-77: один вентилятор.","Количество вентиляторов уменьшено."),
    (18,"location","Перенос обратной решетки","529","RET-78","VENT","Решетка RET-78 расположена у внутренней стены.","Решетка RET-78 перенесена к наружной стене.","Изменено расположение обратной решетки."),
    (19,"configuration","Сокращение ступеней фильтрации","530","AHU-79","VENT","AHU-79: фильтры G4 + F7.","AHU-79: только фильтр F7.","Удалена первая ступень фильтрации."),
    (20,"single_room","Пропуск локальной вытяжки в одном кабинете","531","EX-80","VENT","Локальная вытяжка EX-80 обязательна в помещениях 531, 631.","Локальная вытяжка EX-80 показана только в помещении 631.","В одном из нескольких обязательных помещений отсутствует локальная вытяжка."),
    (21,"configuration","Удаление байпаса теплообменника","532","BP-81","HEAT","Для теплообменника BP-81 предусмотреть байпас.","Теплообменник BP-81 подключен без байпаса.","Удален байпас теплообменника."),
    (22,"location","Перенос насоса на другой уровень","533","P-82","PUMP","Насос P-82 установить в подвале на отм. -3.000.","Насос P-82 установлен на 1 этаже +0.000.","Насос перенесен на другой уровень."),
    (23,"cross_sheet","Несоответствие марки в плане и спецификации","534","EQ-83","EQ","В ПД оборудование имеет марку EQ-83-A.","На плане РД указана EQ-83-A, но в спецификации EQ-83-B.","Внутри РД возникло несоответствие марки относительно ПД."),
    (24,"parameter","Снижение расчетного расхода воздуха","535","AIR-84","VENT","Расчетный расход AIR-84: 1200 м3/ч.","Расчетный расход AIR-84: 850 м3/ч.","Расход воздуха снижен."),
    (25,"connection","Перестановка подачи и обратки","536","H-85","HEAT","H-85: T1 - подача, T2 - обратка.","H-85: T1 - обратка, T2 - подача.","Подача и обратка поменяны местами."),
    (26,"control","Удаление электропривода клапана","537","ACT-86","CTRL","Клапан ACT-86 оснащен электроприводом M-24V.","Клапан ACT-86 указан без электропривода.","Удален электропривод клапана."),
    (27,"parameter","Уменьшение сервисного прохода","538","CLR-87","LAYOUT","Перед оборудованием CLR-87 оставить сервисный проход 900 мм.","Перед оборудованием CLR-87 предусмотрен проход 450 мм.","Сервисный проход уменьшен."),
    (28,"negative_control","Контроль без нарушения","540","NEG-91","NEG","Система NEG-91: расход 600 м3/ч, клапан BV установлен, подключение к FAN-C.","Система NEG-91: расход 600 м3/ч, клапан BV установлен, подключение к FAN-C.",""),
    (29,"negative_control","Контроль без нарушения","541","NEG-92","NEG","Система NEG-92: расход 600 м3/ч, клапан BV установлен, подключение к FAN-C.","Система NEG-92: расход 600 м3/ч, клапан BV установлен, подключение к FAN-C.",""),
    (30,"negative_control","Контроль без нарушения","542","NEG-93","NEG","Система NEG-93: расход 600 м3/ч, клапан BV установлен, подключение к FAN-C.","Система NEG-93: расход 600 м3/ч, клапан BV установлен, подключение к FAN-C.",""),
]

LESSONS = {
    "deletion": "Проверяй каждый обязательный элемент ПД в соответствующем scope РД; ненаблюдаемость сначала является подозрением, а не доказанным отсутствием.",
    "absence": "Подтверждай отсутствие только после проверки достаточного scope РД, смежных листов, примечаний и спецификаций.",
    "quantity": "Сравнивай количество элементов по помещению и системе, а не только сам факт наличия.",
    "type_change": "После связывания сущностей сравнивай функциональный тип и марку оборудования.",
    "parameter": "После сопоставления одной и той же системы сравнивай численные и номинальные параметры.",
    "location": "После сопоставления сущности сравнивай положение, отметку и функциональную зону.",
    "connection": "Сравнивай связность source-target и назначение веток, а не только наличие компонентов.",
    "configuration": "Сравнивай порядок секций, состав узла и топологию как отдельный класс изменений.",
    "single_room": "Для требований на несколько помещений проверяй каждую целевую комнату независимо.",
    "cross_sheet": "Межлистовые утверждения подтверждай по плану, схеме и спецификации до окончательного вывода.",
    "control": "Сравнивай логику управления, тип датчиков и наличие исполнительных приводов.",
    "negative_control": "Если связанные значения ПД и РД совпадают, не придумывай нарушение.",
}


def _font() -> Path:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise RuntimeError("Unicode font not found; install Arial/Calibri/DejaVu Sans")


def _wrap(text: str, width: int = 88) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)


def _write(page: fitz.Page, font: Path, x: float, y: float, text: str, size: float) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname="nad", fontfile=str(font))


def _paragraph(page: fitz.Page, font: Path, x: float, y: float, text: str, size: float = 10, width: int = 88, leading: int = 15) -> float:
    for line in _wrap(text, width):
        _write(page, font, x, y, line, size)
        y += leading
    return y


def _pdf(path: Path, case: tuple, *, rd: bool) -> None:
    idx, category, title, room, mark, system, pd_text, rd_text, _ = case
    text = rd_text if rd else pd_text
    stage = "РД" if rd else "П"
    font = _font()
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    _write(p1, font, 40, 42, f"SYN-{idx:03d} - {title}", 16)
    _write(p1, font, 40, 63, f"Стадия {stage} | Синтетический учебный документ | Лист 1", 9)
    p1.draw_line((40,72),(555,72), width=.7)
    _write(p1, font, 40, 105, "ОБЩИЕ УКАЗАНИЯ", 12)
    y = _paragraph(p1, font, 40, 132, text)
    _write(p1, font, 40, y+18, "КОНТРОЛЬНЫЕ ДАННЫЕ", 11)
    y = _paragraph(p1, font, 50, y+43, f"Помещение: {room}", 9)
    y = _paragraph(p1, font, 50, y, f"Марка / система: {mark} / {system}", 9)
    _paragraph(p1, font, 50, y, "Соседние системы и помещения не изменяются.", 8.5)
    _write(p1, font, 500, 815, "1", 9)

    p2 = doc.new_page(width=595, height=842)
    _write(p2, font, 40, 42, f"SYN-{idx:03d} - {title}", 16)
    _write(p2, font, 40, 63, f"Стадия {stage} | Схема/план | Лист 2", 9)
    p2.draw_line((40,72),(555,72), width=.7)
    rooms = (room, str(int(room)+100), str(int(room)+200))
    for pos, r in enumerate(rooms):
        x = 55 + pos * 163
        p2.draw_rect(fitz.Rect(x,135,x+145,255), width=1)
        _write(p2, font, x+7, 155, f"Пом. {r}", 9.5)
        _write(p2, font, x+7, 183, mark if pos == 0 else f"DIST-{pos}", 8.5)
        _paragraph(p2, font, x+7, 207, (text if pos == 0 else "Без изменений")[:90], 7.2, width=22, leading=13)
    _write(p2, font, 55, 310, "ПРИМЕЧАНИЕ К СХЕМЕ", 9)
    _paragraph(p2, font, 55, 334, text, 8.2, width=91, leading=12)
    _write(p2, font, 500, 815, "2", 9)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def generate(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"version":"1.1","benchmark_safe":True,"cases":[]}
    for case in CASES:
        idx, category, title, room, mark, system, pd_text, rd_text, fact = case
        cid = f"SYN-{idx:03d}"
        cdir = out / cid
        pd_path, rd_path = cdir / f"{cid}_PD.pdf", cdir / f"{cid}_RD.pdf"
        _pdf(pd_path, case, rd=False); _pdf(rd_path, case, rd=True)
        positive = category != "negative_control"
        expected = None if not positive else {"type":category,"room":room,"mark":mark,"system":system,"pd_page":1,"rd_page":1,"fact":fact}
        gt = {"case_id":cid,"category":category,"positive":positive,"expected":expected,
              "allowed_training_abstraction":{"category":category,"lesson":LESSONS[category]}}
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "ground_truth.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["cases"].append({"case_id":cid,"category":category,"positive":positive,
            "pd":str(pd_path.relative_to(out)),"rd":str(rd_path.relative_to(out)),
            "ground_truth":str((cdir / "ground_truth.json").relative_to(out))})
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    manifest = generate(args.out)
    print(f"Generated {len(manifest['cases'])} synthetic PD/RD pairs in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
