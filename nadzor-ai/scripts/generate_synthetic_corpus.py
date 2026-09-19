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

CASES = json.loads((ROOT / "data" / "synthetic_curricula" / "base.json").read_text(encoding="utf-8"))

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
        _pdf(pd_path, case, rd=False)
        _pdf(rd_path, case, rd=True)
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
