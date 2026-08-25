"""Сквозная проверка на реальных образцах из этой сессии: подтверждает, что
исправление (vision-fallback для растрового штампа) действительно чинит
ложный вывод «0 пар — разные разделы» из более ранней части сессии.

rd_floor1.pdf / rd_floor2_heating.pdf — это исполнительные чертежи раздела ОВ
(«АНО/150321/1-РД-ОВ1» и «...-РД-ОВ2.1» — подтверждено визуально в этой
сессии), но их штамп — картинка без текстового слоя. Без vision эти файлы
классифицируются как discipline_code=None, и гейтинг по разделу не
применяется (см. test_matching.py: no-code -> ungated fallback на текст).
С vision-fallback (здесь замокан ответом, который реально написан на
картинке штампа) — оба файла корректно получают код «ОВ», такой же, как у
177-страничного тома спецификации.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classification import classify_document
from app.documents import extract_document_facts
from app.matching import DocumentInput, match_page_pairs

SAMPLE_DIR = Path(
    "/tmp/claude-0/-home-user-DmitryKDI/0870a421-62c2-59a8-8978-c9163f520b16/scratchpad"
)

# То, что реально написано на картинке штампа каждого файла (прочитано визуально
# в этой сессии) — здесь имитирует ответ настоящей vision-модели.
REAL_STAMP_CODES = {
    "rd_floor1.pdf": "ОВ",           # АНО/150321/1-РД-ОВ1
    "rd_floor2_heating.pdf": "ОВ",   # АНО/150321/1-РД-ОВ2.1
}


def make_vision_fn(filename: str):
    def fn(png_bytes: bytes):
        assert len(png_bytes) > 500
        return REAL_STAMP_CODES.get(filename)
    return fn


def test_without_vision_stamp_is_unclassified():
    for name in ["rd_floor1.pdf", "rd_floor2_heating.pdf"]:
        result = classify_document(str(SAMPLE_DIR / name), name)
        assert result.discipline_code is None, (name, result)
    print("OK: without vision, real as-built ОВ sheets stay unclassified (confirms the bug this fixes)")


def test_with_vision_both_sheets_classified_as_ov():
    codes = {}
    for name in ["rd_floor1.pdf", "rd_floor2_heating.pdf"]:
        result = classify_document(str(SAMPLE_DIR / name), name, vision_stamp_fn=make_vision_fn(name))
        codes[name] = result.discipline_code
    assert codes == {"rd_floor1.pdf": "ОВ", "rd_floor2_heating.pdf": "ОВ"}, codes
    print("OK: with vision fallback, both real as-built sheets correctly classified as ОВ")


def test_pairing_with_ov_spec_volume_uses_discipline_gate():
    # Настоящее имя файла (как реально называют том инспекторы) — не ASCII-копия
    # из более ранних браузерных тестов. В бэкенде (в отличие от Playwright)
    # кириллические имена файлов не создают проблем, поэтому здесь используем
    # реальное имя и проверяем сигнал по имени файла, как в реальном сценарии.
    pd_name = "pd_tom542_real_name_ОВ.pdf"
    pd_facts = extract_document_facts(str(SAMPLE_DIR / pd_name), pd_name)
    pd_code = classify_document(str(SAMPLE_DIR / pd_name), pd_name).discipline_code
    assert pd_code == "ОВ", pd_code  # код из имени файла — «...Том 5.4.2 ОВ (1).pdf»

    rd_names = ["rd_floor1.pdf", "rd_floor2_heating.pdf", "rd_basement.pdf"]
    rd_docs = []
    for name in rd_names:
        facts = extract_document_facts(str(SAMPLE_DIR / name), name)
        result = classify_document(str(SAMPLE_DIR / name), name, vision_stamp_fn=make_vision_fn(name))
        rd_docs.append(DocumentInput(name, facts.pages, facts.text_facts, [], result.discipline_code, facts.page_kinds))

    before = [DocumentInput("pd_tom542_ov.pdf", pd_facts.pages, pd_facts.text_facts, [], pd_code, pd_facts.page_kinds)]
    pairs = match_page_pairs(before, rd_docs)

    assert len(pairs) > 0, "expected at least some pairs"
    # Каждая пара — либо чертёж с чертежом, либо текст с текстом, никогда не
    # вперемешку (см. classification.classify_page_kind).
    for p in pairs:
        before_kind = pd_facts.page_kinds.get(p.before_page)
        after_kind = rd_docs[p.after_file_idx].page_kinds.get(p.after_page)
        assert before_kind == after_kind == p.page_kind, (p, before_kind, after_kind)

    # Оба реальных исполнительных чертежа раздела ОВ должны попасть хотя бы в
    # одну пару, без пометки расхождения раздела.
    ov_pairs = [p for p in pairs if rd_docs[p.after_file_idx].name in ("rd_floor1.pdf", "rd_floor2_heating.pdf")]
    assert {rd_docs[p.after_file_idx].name for p in ov_pairs} == {"rd_floor1.pdf", "rd_floor2_heating.pdf"}
    for p in ov_pairs:
        assert p.discipline_mismatch is False, p
        assert p.page_kind == "drawing", p  # оба реальных листа — исполнительные чертежи A0
    print(f"OK: {len(pairs)} pairs found, all page-kind consistent, both real ОВ as-built sheets paired without discipline mismatch")


if __name__ == "__main__":
    test_without_vision_stamp_is_unclassified()
    test_with_vision_both_sheets_classified_as_ov()
    test_pairing_with_ov_spec_volume_uses_discipline_gate()
    print("ALL PASS")
