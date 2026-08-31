import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.requirement_registry import (
    Requirement,
    extract_coded_requirements,
    extract_predicate_requirements,
    extract_requirements,
)

SAMPLE_DIR = Path("/home/user/nadzor_sample")
PD_PZ = SAMPLE_DIR / "V2_01-05-04-02-07_Том 5.4.2 ОВ (1).pdf"


def test_coded_item_extracts_rooms_and_code():
    """Реальная форма из пояснительной записки: тире-пункт списка систем
    противодымной защиты."""
    text_facts = [{"page": 16, "text": (
        "предусматриваются:\n"
        "- из поэтажных коридоров, пом. 108, 201 (ВД1);\n"
        "- из поэтажных коридоров, пом. 139, 227, 309 (ВД2);\n"
    )}]
    reqs = extract_coded_requirements(text_facts)
    assert len(reqs) == 2
    assert reqs[0] == Requirement(rooms=["108", "201"], page=16,
                                   sentence="- из поэтажных коридоров, пом. 108, 201 (ВД1);",
                                   code="ВД1")
    assert reqs[1].code == "ВД2" and reqs[1].rooms == ["139", "227", "309"]
    print("OK: тире-пункты с кодом системы разобраны по отдельности")


def test_coded_item_merges_multiple_pom_runs_in_one_item():
    """Один пункт списка может содержать несколько «пом. ...» через «и» —
    оба набора номеров относятся к одному коду системы (наблюдение с
    реального файла: пункт ВД5)."""
    text_facts = [{"page": 16, "text": (
        "- из поэтажных коридоров, пом. 138, 219, 304 и рекреация, пом. 275 (ВД5);\n"
    )}]
    reqs = extract_coded_requirements(text_facts)
    assert len(reqs) == 1
    assert reqs[0].code == "ВД5"
    assert reqs[0].rooms == ["138", "219", "304", "275"]
    print("OK: несколько «пом.» внутри одного пункта объединены под одним кодом")


def test_coded_item_does_not_span_into_next_list_entry():
    """Тире внутри обычного текста (дефис в слове, «не-» и т.п.) не должен
    становиться ложной границей пункта, из-за которой поиск кода уедет в
    следующий пункт списка и захватит чужие номера помещений — это была
    реальная ошибка первой версии регулярки (см. историю сессии): без
    требования «тире в начале строки» «- ... (КОД);» подхватывал номера
    из совершенно не связанного предложения несколькими абзацами раньше."""
    text_facts = [{"page": 10, "text": (
        "В помещениях для МГН (пом. 267, 270) предусмотрена система "
        "подогрева полов, совмещенная с системой радиаторного отопления.\n"
        "Далее по тексту, без всякого списка.\n"
        "- из поэтажных коридоров, пом. 108, 201 (ВД1);\n"
    )}]
    reqs = extract_coded_requirements(text_facts)
    assert len(reqs) == 1
    assert reqs[0].rooms == ["108", "201"]
    print("OK: посторонний текст перед пунктом списка не даёт ложного склеивания")


def test_ignores_dash_item_without_room_reference():
    text_facts = [{"page": 5, "text": "- вентилятор канальный, 1 шт. (В14);\n"}]
    assert extract_coded_requirements(text_facts) == []
    print("OK: пункт списка без «пом.» не создаёт запись реестра")


def test_predicate_requirement_found_with_perfective_verb():
    """Реальное предложение (нарушение №2): «предусмотрена» — совершенный
    вид, корень «предусмотр-»."""
    text_facts = [{"page": 10, "text": (
        "Приборы должны быть выполнены в травмобезопасном исполнении. "
        "В помещениях раздевальных, санузлов и душевых для МГН "
        "(пом. 267, 270, 271, 272) предусмотрена система подогрева полов "
        "(теплые полы) совмещенная с системой радиаторного отопления. "
        "Регулирование температуры пола осуществляется регуляторами."
    )}]
    reqs = extract_predicate_requirements(text_facts)
    assert len(reqs) == 1
    assert reqs[0].rooms == ["267", "270", "271", "272"]
    assert reqs[0].code is None
    assert "подогрева полов" in reqs[0].sentence
    assert "Регулирование" not in reqs[0].sentence
    print("OK: требование без кода найдено, предложение обрезано по границе")


def test_predicate_requirement_found_with_imperfective_verb():
    """Реальное предложение: «предусматривается» — несовершенный вид,
    корень «предусматр-» (другая гласная — не то же слово, что выше)."""
    text_facts = [{"page": 11, "text": (
        "Освещённость должна составлять не менее 75%. "
        "В помещении горячего цеха (пом. 189) в не рабочее время "
        "предусматривается поддержание температуры внутреннего воздуха "
        "равной +12 С. Разводка труб выполняется скрыто."
    )}]
    reqs = extract_predicate_requirements(text_facts)
    assert len(reqs) == 1
    assert reqs[0].rooms == ["189"]
    print("OK: несовершенный вид глагола-предиката тоже распознаётся")


def test_room_reference_without_predicate_is_not_a_requirement():
    """Реальный случай той же формы «(пом. N)», но БЕЗ глагола-требования —
    это указание места объекта, не требование к помещению (наблюдение с
    реального файла: «... установлена в подвале здания (пом. 007).»)."""
    text_facts = [{"page": 13, "text": (
        "Установка П20 установлена в подвале здания (пом. 007). "
        "В помещении ИТП устанавливается приточная установка П19."
    )}]
    assert extract_predicate_requirements(text_facts) == []
    print("OK: место без глагола-предиката требования не даёт ложной находки")


def test_table_legend_caption_is_not_a_requirement():
    """Реальный случай: подпись легенды на схеме («Отопление ... (пом. 167)»)
    — не предложение и не требование, глагола нет."""
    text_facts = [{"page": 20, "text": "Отопление многосветного пространства (пом. 167)"}]
    assert extract_predicate_requirements(text_facts) == []
    print("OK: подпись легенды без предиката отфильтрована")


def test_extract_requirements_combines_both_forms():
    text_facts = [{"page": 16, "text": "- из поэтажных коридоров, пом. 108, 201 (ВД1);\n"}]
    text_facts2 = [{"page": 10, "text": (
        "Общие указания. В помещениях для МГН (пом. 267, 270) "
        "предусмотрена система подогрева полов."
    )}]
    combined = text_facts + text_facts2
    reqs = extract_requirements(combined)
    codes = {r.code for r in reqs}
    assert "ВД1" in codes
    assert any(r.code is None for r in reqs)
    print("OK: обе формы объединяются в общий реестр")


# --------------------------------------------------------------------------
# smoke на реальном ПЗ
# --------------------------------------------------------------------------

def test_real_pz_yields_both_forms():
    """На реальной пояснительной записке (Том 5.4.2 ОВ) обе формы дают
    непустой, но по-разному устроенный результат: список ВД/ПД с кодами —
    десятки записей, требования без кода — единицы (структурно редкая,
    но именно та форма, что несёт нарушение №2)."""
    if not PD_PZ.exists():
        print("SKIP: нет файла", PD_PZ)
        return
    import pymupdf
    doc = pymupdf.open(PD_PZ)
    try:
        text_facts = [{"page": i, "text": page.get_text()} for i, page in enumerate(doc)]
    finally:
        doc.close()

    coded = extract_coded_requirements(text_facts)
    predicate = extract_predicate_requirements(text_facts)

    assert len(coded) >= 20, f"ожидали десятки пунктов списка ВД/ПД, получили {len(coded)}"
    assert all(r.code for r in coded)
    assert len(predicate) >= 1, "ожидали хотя бы одно требование без кода на реальном файле"
    assert all(r.code is None for r in predicate)
    print(f"OK: реальный ПЗ — {len(coded)} пунктов с кодом, {len(predicate)} без кода")


if __name__ == "__main__":
    test_coded_item_extracts_rooms_and_code()
    test_coded_item_merges_multiple_pom_runs_in_one_item()
    test_coded_item_does_not_span_into_next_list_entry()
    test_ignores_dash_item_without_room_reference()
    test_predicate_requirement_found_with_perfective_verb()
    test_predicate_requirement_found_with_imperfective_verb()
    test_room_reference_without_predicate_is_not_a_requirement()
    test_table_legend_caption_is_not_a_requirement()
    test_extract_requirements_combines_both_forms()
    test_real_pz_yields_both_forms()
    print("ALL PASS")
