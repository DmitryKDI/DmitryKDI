"""Тесты для requirement_cross_check.py — сверка требований ПД↔РД (Г.33)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import DocumentInput
from app.requirement_cross_check import cross_check_requirements, render_requirement_cross_check_report
from app.triangulation import signals_from_requirement_cross_check, triangulate, CONFIRMED, CANDIDATE

SAMPLE_DIR = Path("/home/user/nadzor_sample")
PD_PZ = SAMPLE_DIR / "V2_01-05-04-02-07_Том 5.4.2 ОВ (1).pdf"
RD_OV1_A = SAMPLE_DIR / "АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf"
RD_OV1_B = SAMPLE_DIR / "АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-101-676.pdf"


def tf(page, text):
    return {"page": page, "text": text}


def test_coded_requirement_confirmed_when_code_present_in_rd():
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(16, "- из поэтажных коридоров, пом. 108, 201 (ВД1);"),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(50, "Проект: вентилятор дымоудаления ВД1, тип ВРАН6-8000."),
    ])]
    result = cross_check_requirements(before, after)
    assert result.total_coded == 1
    assert result.coded_confirmed == 1 and result.coded_missing == 0
    assert result.findings[0].finding_type == "code_confirmed_in_rd"
    print("OK: код требования найден в тексте РД — code_confirmed_in_rd")


def test_coded_requirement_missing_when_code_absent_from_rd():
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(16, "- из поэтажных коридоров, пом. 108, 201 (ВД1);"),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(50, "Проект: вентилятор общеобменной вентиляции П3."),
    ])]
    result = cross_check_requirements(before, after)
    assert result.coded_missing == 1 and result.coded_confirmed == 0
    f = result.findings[0]
    assert f.finding_type == "code_missing_in_rd"
    assert f.severity == "существенно"
    assert "108" in f.detail and "201" in f.detail
    print("OK: код требования не найден в тексте РД — code_missing_in_rd, существенно")


def test_coded_requirement_word_boundary_no_false_match():
    """Код «ВД1» не должен ложно совпасть внутри «ВД11» или «ВД10» —
    иначе подтверждение окажется случайным совпадением подстроки."""
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(16, "- из поэтажных коридоров, пом. 108, 201 (ВД1);"),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(50, "Проект: вентилятор ВД11, тип ВРАН6-8000. Также ВД10 на кровле."),
    ])]
    result = cross_check_requirements(before, after)
    assert result.coded_missing == 1 and result.coded_confirmed == 0
    print("OK: «ВД1» не путается с «ВД11»/«ВД10» — границы кода соблюдены")


def test_predicate_requirement_confirmed_when_same_rooms_repeated_in_rd():
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(10, "Общие указания. В помещениях для МГН (пом. 267, 270) "
                "предусмотрена система подогрева полов."),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(5, "В помещениях (пом. 267, 270) предусмотрена система подогрева полов, "
              "тип кабеля и шаг раскладки согласно схеме."),
    ])]
    result = cross_check_requirements(before, after)
    assert result.predicate_confirmed == 1 and result.predicate_missing == 0
    assert result.findings[0].finding_type == "predicate_confirmed_in_rd"
    print("OK: то же требование без кода повторено в РД — predicate_confirmed_in_rd")


def test_predicate_requirement_missing_flagged_as_candidate_not_verdict():
    """Реальная форма нарушения №2: требование в прозе ПД есть, в тексте
    РД такого предложения нет вовсе (несоответствие — на чертеже, не в
    тексте РД). Находка — кандидат на графическую проверку, а не готовый
    вердикт «нарушение»: detail должен явно это оговаривать."""
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(10, "Общие указания. В помещениях для МГН (пом. 267, 270) "
                "предусмотрена система подогрева полов."),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(5, "Экспликация помещений и спецификация оборудования, без упоминания полов."),
    ])]
    result = cross_check_requirements(before, after)
    assert result.predicate_missing == 1 and result.predicate_confirmed == 0
    f = result.findings[0]
    assert f.finding_type == "predicate_missing_in_rd"
    assert "не равно отсутствию на чертеже" in f.detail
    print("OK: отсутствие в тексте РД помечено как кандидат, а не вердикт")


def test_render_report_contains_counts():
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(16, "- из поэтажных коридоров, пом. 108, 201 (ВД1);"),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ничего похожего")])]
    result = cross_check_requirements(before, after)
    report = render_requirement_cross_check_report(result)
    assert "Сверка требований ПД↔РД" in report
    assert "code_missing_in_rd" in report
    print("OK: отчёт содержит секции по типам находок")


def test_triangulation_adapter_only_emits_missing_signals():
    """`*_confirmed_in_rd` — не сигнал о расхождении, только `*_missing_in_rd`
    должно попасть в триангуляцию (тот же принцип, что у остальных
    signals_from_* адаптеров)."""
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(16, "- из поэтажных коридоров, пом. 108, 201 (ВД1);"),
        tf(10, "Общие указания. В помещениях для МГН (пом. 267, 270) "
                "предусмотрена система подогрева полов."),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ничего похожего ни на что")])]
    result = cross_check_requirements(before, after)
    signals = signals_from_requirement_cross_check(result.findings)
    domains = {s.domain for s in signals}
    assert domains == {"room", "requirement_code"}
    room_keys = {s.key for s in signals if s.domain == "room"}
    assert room_keys == {"267", "270"}
    code_keys = {s.key for s in signals if s.domain == "requirement_code"}
    assert code_keys == {"ВД1"}
    print("OK: адаптер отдаёт сигналы только по missing-находкам, не по confirmed")


def test_triangulation_combines_with_other_room_sources():
    """Ровно то, что вручную произошло с нарушением №2 в этой сессии:
    сигнал по прозе-без-кода складывается с независимым сигналом другого
    источника по тому же номеру помещения — вместе они дают confirmed."""
    before = [DocumentInput("pd.pdf", 1, text_facts=[
        tf(10, "Общие указания. В помещениях для МГН (пом. 270) "
                "предусмотрена система подогрева полов."),
    ])]
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ничего похожего")])]
    result = cross_check_requirements(before, after)
    signals = signals_from_requirement_cross_check(result.findings)
    from app.triangulation import Signal
    signals = list(signals) + [Signal(source="room_registry", domain="room", key="270", detail="доп. сигнал")]
    confirmations = triangulate(signals)
    room_270 = [c for c in confirmations if c.domain == "room" and c.key == "270"]
    assert room_270 and room_270[0].status == CONFIRMED
    print("OK: сигнал requirement_prose складывается с другим источником в confirmed")


# --------------------------------------------------------------------------
# smoke на реальном комплекте
# --------------------------------------------------------------------------

def test_real_pd_vs_rd_smoke():
    """На реальном комплекте (ПЗ + оба тома РД-ОВ1) сверка не падает и
    даёт непустой, структурно осмысленный результат — сами числа не
    проверяются (Г.24), только форма."""
    if not (PD_PZ.exists() and RD_OV1_A.exists() and RD_OV1_B.exists()):
        print("SKIP: нет файлов реального комплекта")
        return
    import pymupdf

    def load(path):
        doc = pymupdf.open(path)
        try:
            return [{"page": i, "text": page.get_text()} for i, page in enumerate(doc)]
        finally:
            doc.close()

    before = [DocumentInput("pd.pdf", 1, text_facts=load(PD_PZ))]
    after = [
        DocumentInput("rd_a.pdf", 1, text_facts=load(RD_OV1_A)),
        DocumentInput("rd_b.pdf", 1, text_facts=load(RD_OV1_B)),
    ]
    result = cross_check_requirements(before, after)
    assert result.total_coded >= 20
    assert result.coded_confirmed + result.coded_missing == result.total_coded
    assert result.predicate_confirmed + result.predicate_missing == result.total_predicate
    signals = signals_from_requirement_cross_check(result.findings)
    confirmations = triangulate(signals)
    print(f"OK: реальный комплект — коды {result.coded_confirmed}/{result.total_coded} подтверждено, "
          f"без кода {result.predicate_confirmed}/{result.total_predicate} подтверждено, "
          f"{len(confirmations)} triangulation-ключей затронуто")


if __name__ == "__main__":
    test_coded_requirement_confirmed_when_code_present_in_rd()
    test_coded_requirement_missing_when_code_absent_from_rd()
    test_coded_requirement_word_boundary_no_false_match()
    test_predicate_requirement_confirmed_when_same_rooms_repeated_in_rd()
    test_predicate_requirement_missing_flagged_as_candidate_not_verdict()
    test_render_report_contains_counts()
    test_triangulation_adapter_only_emits_missing_signals()
    test_triangulation_combines_with_other_room_sources()
    test_real_pd_vs_rd_smoke()
    print("ALL PASS")
