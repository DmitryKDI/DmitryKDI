"""Тесты для requirement_cross_check.py — сверка требований ПД↔РД (Г.33/Г.36).

`cross_check_requirements` теперь принимает уже готовый `list[Requirement]`
напрямую (источник — regex или ЛЛМ, эта функция не знает и не должна
знать), а не сама извлекает требования из `before_files` — развязка
источника извлечения от сверки против РД, см. requirement_cross_check.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import DocumentInput
from app.requirement_cross_check import (
    cross_check_general_requirements,
    cross_check_requirements,
    render_general_requirement_cross_check_report,
    render_requirement_cross_check_report,
)
from app.requirement_registry import Requirement
from app.triangulation import signals_from_requirement_cross_check, triangulate, CONFIRMED

SAMPLE_DIR = Path("/home/user/nadzor_sample")
PD_PZ = SAMPLE_DIR / "V2_01-05-04-02-07_Том 5.4.2 ОВ (1).pdf"
RD_OV1_A = SAMPLE_DIR / "АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf"
RD_OV1_B = SAMPLE_DIR / "АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-101-676.pdf"


def tf(page, text):
    return {"page": page, "text": text}


def req(rooms, code=None, page=1, sentence="требование"):
    return Requirement(rooms=rooms, page=page, sentence=sentence, code=code)


def test_coded_requirement_confirmed_when_code_present_in_rd():
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(50, "Проект: вентилятор дымоудаления ВД1, тип ВРАН6-8000."),
    ])]
    result = cross_check_requirements([req(["108", "201"], code="ВД1")], after)
    assert result.total_coded == 1
    assert result.coded_confirmed == 1 and result.coded_missing == 0
    assert result.findings[0].finding_type == "code_confirmed_in_rd"
    print("OK: код требования найден в тексте РД — code_confirmed_in_rd")


def test_coded_requirement_missing_when_code_absent_from_rd():
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(50, "Проект: вентилятор общеобменной вентиляции П3."),
    ])]
    result = cross_check_requirements([req(["108", "201"], code="ВД1")], after)
    assert result.coded_missing == 1 and result.coded_confirmed == 0
    f = result.findings[0]
    assert f.finding_type == "code_missing_in_rd"
    assert f.severity == "существенно"
    assert "108" in f.detail and "201" in f.detail
    print("OK: код требования не найден в тексте РД — code_missing_in_rd, существенно")


def test_coded_requirement_word_boundary_no_false_match():
    """Код «ВД1» не должен ложно совпасть внутри «ВД11» или «ВД10» —
    иначе подтверждение окажется случайным совпадением подстроки."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(50, "Проект: вентилятор ВД11, тип ВРАН6-8000. Также ВД10 на кровле."),
    ])]
    result = cross_check_requirements([req(["108", "201"], code="ВД1")], after)
    assert result.coded_missing == 1 and result.coded_confirmed == 0
    print("OK: «ВД1» не путается с «ВД11»/«ВД10» — границы кода соблюдены")


def test_requirement_without_code_always_needs_visual_check():
    """Требование без кода не сверяется текстом вообще — у требования без
    кода нет короче-чем-предложение якоря для текстового поиска, и попытка
    сверить текст-с-текстом заново упирается в проблему формата (Г.36):
    любая формулировка идёт прямиком в кандидаты на зрение."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(5, "В помещениях (пом. 267, 270) предусмотрена система подогрева полов — "
              "дословное совпадение с ПД, но это ничего не меняет."),
    ])]
    result = cross_check_requirements([req(["267", "270"], code=None)], after)
    assert result.total_no_code == 1
    f = result.findings[0]
    assert f.finding_type == "no_code_visual_check_needed"
    assert f.severity == "существенно"
    print("OK: требование без кода всегда становится кандидатом на зрение, даже при текстовом совпадении в РД")


def test_render_report_contains_counts():
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ничего похожего")])]
    result = cross_check_requirements([req(["108", "201"], code="ВД1")], after)
    report = render_requirement_cross_check_report(result)
    assert "Сверка требований ПД↔РД" in report
    assert "code_missing_in_rd" in report
    print("OK: отчёт содержит секции по типам находок")


def test_triangulation_adapter_only_emits_missing_signals():
    """`code_confirmed_in_rd` — не сигнал о расхождении; `code_missing_in_rd`
    и `no_code_visual_check_needed` — да (тот же принцип, что у остальных
    signals_from_* адаптеров: сигнал — это находка о несовпадении, не
    запись о подтверждённом соответствии)."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ничего похожего ни на что")])]
    pd_requirements = [
        req(["108", "201"], code="ВД1"),
        req(["267", "270"], code=None),
    ]
    result = cross_check_requirements(pd_requirements, after)
    signals = signals_from_requirement_cross_check(result.findings)
    domains = {s.domain for s in signals}
    assert domains == {"room", "requirement_code"}
    room_keys = {s.key for s in signals if s.domain == "room"}
    assert room_keys == {"267", "270"}
    code_keys = {s.key for s in signals if s.domain == "requirement_code"}
    assert code_keys == {"ВД1"}
    print("OK: адаптер отдаёт сигналы только по missing/no_code-находкам, не по confirmed")


def test_triangulation_combines_with_other_room_sources():
    """Требование без кода складывается в триангуляции с независимым
    сигналом другого источника по тому же номеру помещения — вместе они
    дают confirmed (ровно так вручную нашлось нарушение №2 этой сессии)."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ничего похожего")])]
    result = cross_check_requirements([req(["270"], code=None)], after)
    signals = signals_from_requirement_cross_check(result.findings)
    from app.triangulation import Signal
    signals = list(signals) + [Signal(source="room_registry", domain="room", key="270", detail="доп. сигнал")]
    confirmations = triangulate(signals)
    room_270 = [c for c in confirmations if c.domain == "room" and c.key == "270"]
    assert room_270 and room_270[0].status == CONFIRMED
    print("OK: сигнал requirement_prose складывается с другим источником в confirmed")


def test_duplicate_codes_counted_once():
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ВД1 упомянут")])]
    pd_requirements = [req(["108"], code="ВД1"), req(["201"], code="ВД1")]
    result = cross_check_requirements(pd_requirements, after)
    assert result.total_coded == 2  # оба требования учтены в счётчике...
    assert result.coded_confirmed == 1  # ...но код проверяется один раз
    print("OK: повторяющийся код не сверяется с текстом РД дважды")


def test_requirements_with_different_room_label_styles_do_not_break_cross_check():
    """Требования из разных источников (regex vs LLM) могут называть
    помещения по-разному (номер, название зоны) — сверка не должна
    предполагать конкретный формат."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ПД5 в проекте")])]
    pd_requirements = [
        req(["Зона А"], code="ПД5"),
        req(["офис 12", "офис 14"], code=None),
    ]
    result = cross_check_requirements(pd_requirements, after)
    assert result.coded_confirmed == 1
    assert result.total_no_code == 1
    print("OK: нечисловые/смешанные обозначения помещений не ломают сверку")


# --------------------------------------------------------------------------
# Сверка формы 3 по токену (Г.48)
# --------------------------------------------------------------------------

def test_general_requirement_gost_token_confirmed_in_rd():
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(15, "Воздуховоды выполняются из оцинкованной стали по ГОСТ 14918-80."),
    ])]
    reqs = [req([], sentence="Воздуховоды со специальными требованиями выполняются из "
                             "оцинкованной стали по ГОСТ 14918-80.")]
    result = cross_check_general_requirements(reqs, after)
    assert result.with_token == 1 and result.token_confirmed == 1
    assert result.findings[0].finding_type == "token_confirmed_in_rd"
    assert result.findings[0].token == "ГОСТ 14918-80"
    print("OK: номер ГОСТ найден в тексте РД — token_confirmed_in_rd")


def test_general_requirement_gost_token_missing_in_rd():
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(15, "ничего похожего")])]
    reqs = [req([], sentence="Трубопроводы выполняются по ГОСТ 3262-75.")]
    result = cross_check_general_requirements(reqs, after)
    assert result.token_missing == 1
    f = result.findings[0]
    assert f.finding_type == "token_missing_in_rd" and f.severity == "существенно"
    print("OK: номер ГОСТ не найден в тексте РД — token_missing_in_rd, существенно")


def test_general_requirement_quoted_brand_token_tolerates_quote_style():
    """РД может использовать другие кавычки (или вообще без них) — токен
    сравнивается после нормализации, не посимвольно."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[
        tf(20, "Установлены завесы Тепломаш с электроподогревом."),
    ])]
    reqs = [req([], sentence='Предусматривается установка завес фирмы "Тепломаш" с электроподогревом.')]
    result = cross_check_general_requirements(reqs, after)
    assert result.findings[0].finding_type == "token_confirmed_in_rd"
    print("OK: марка в кавычках сверяется после нормализации кавычек")


def test_general_requirement_bare_class_letter_is_not_a_token_alone():
    """Голая буква класса («В») сама по себе — НЕ токен: слишком общая,
    совпадёт почти с любым листом РД. Токеном может быть только фраза
    целиком («класс герметичности «В»»)."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "класс герметичности В указан на листе 5")])]
    reqs = [req([], sentence="Воздуховоды выполняются классом герметичности «В».")]
    result = cross_check_general_requirements(reqs, after)
    assert result.findings[0].token == "классом герметичности «В»"
    print("OK: токен класса — фраза целиком, не голая буква")


def test_general_requirement_without_any_token_is_flagged_for_manual_review_not_escalated():
    """Требование без распознаваемого токена НЕ становится автоматическим
    кандидатом на эскалацию (в отличие от формы 2/no_code_visual_check_needed)
    — только видимая пометка, форма 3 по конструкции шумнее (Г.47)."""
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "что угодно")])]
    reqs = [req([], sentence="Отопительные приборы должны быть выполнены в травмобезопасном исполнении.")]
    result = cross_check_general_requirements(reqs, after)
    assert result.no_token == 1
    assert result.findings[0].finding_type == "no_token_manual_review"
    assert result.findings[0].severity == "незначительно"
    print("OK: требование без токена помечено для ручного просмотра, не эскалировано")


def test_render_general_requirement_cross_check_report_shows_counts():
    after = [DocumentInput("rd.pdf", 1, text_facts=[tf(1, "ничего похожего")])]
    reqs = [req([], sentence="Трубопроводы выполняются по ГОСТ 3262-75.")]
    result = cross_check_general_requirements(reqs, after)
    report = render_general_requirement_cross_check_report(result)
    assert "Сверка общих требований ПД↔РД по токену (Г.48)" in report
    assert "token_missing_in_rd" in report
    print("OK: отчёт формы 3 содержит счётчики и секции по типам находок")


def test_real_pd_vs_rd_general_requirements_smoke():
    """На реальном комплекте (форма 3 из ПЗ, сверка против обоих томов
    РД-ОВ1) сверка не падает, доля требований с токеном небольшая, но
    ненулевая (Г.48 — измерено на этом же файле: ~20-30 из 84)."""
    if not (PD_PZ.exists() and RD_OV1_A.exists() and RD_OV1_B.exists()):
        print("SKIP: нет файлов реального комплекта")
        return
    import pymupdf
    from app.requirement_registry import extract_general_requirements

    def load(path):
        doc = pymupdf.open(path)
        try:
            return [{"page": i, "text": page.get_text()} for i, page in enumerate(doc)]
        finally:
            doc.close()

    pd_text_facts = load(PD_PZ)
    general_requirements = extract_general_requirements(pd_text_facts)
    after = [
        DocumentInput("rd_a.pdf", 1, text_facts=load(RD_OV1_A)),
        DocumentInput("rd_b.pdf", 1, text_facts=load(RD_OV1_B)),
    ]
    result = cross_check_general_requirements(general_requirements, after)
    assert result.total == len(general_requirements)
    assert result.with_token >= 1
    assert result.with_token + result.no_token == result.total
    print(f"OK: реальный комплект — форма 3: {result.total} всего, {result.with_token} с токеном "
          f"({result.token_confirmed} подтверждено, {result.token_missing} не найдено), "
          f"{result.no_token} без токена (ручной просмотр)")


# --------------------------------------------------------------------------
# smoke на реальном комплекте (regex-путь, как честный запасной экстрактор)
# --------------------------------------------------------------------------

def test_real_pd_vs_rd_smoke():
    """На реальном комплекте (ПЗ + оба тома РД-ОВ1) сверка не падает и
    даёт непустой, структурно осмысленный результат — сами числа не
    проверяются (Г.24), только форма. Извлечение здесь — regex-путь
    (requirement_registry.py), потому что в этой среде нет ключа ЛЛМ; сама
    сверка (проверяемая этим тестом) от источника извлечения не зависит."""
    if not (PD_PZ.exists() and RD_OV1_A.exists() and RD_OV1_B.exists()):
        print("SKIP: нет файлов реального комплекта")
        return
    import pymupdf
    from app.requirement_registry import extract_requirements

    def load(path):
        doc = pymupdf.open(path)
        try:
            return [{"page": i, "text": page.get_text()} for i, page in enumerate(doc)]
        finally:
            doc.close()

    pd_text_facts = load(PD_PZ)
    pd_requirements = extract_requirements(pd_text_facts)
    after = [
        DocumentInput("rd_a.pdf", 1, text_facts=load(RD_OV1_A)),
        DocumentInput("rd_b.pdf", 1, text_facts=load(RD_OV1_B)),
    ]
    result = cross_check_requirements(pd_requirements, after)
    assert result.total_coded >= 20
    assert result.coded_confirmed + result.coded_missing == result.total_coded
    signals = signals_from_requirement_cross_check(result.findings)
    confirmations = triangulate(signals)
    print(f"OK: реальный комплект — коды {result.coded_confirmed}/{result.total_coded} подтверждено, "
          f"без кода {result.total_no_code} кандидат(ов) на зрение, "
          f"{len(confirmations)} triangulation-ключей затронуто")


if __name__ == "__main__":
    test_coded_requirement_confirmed_when_code_present_in_rd()
    test_coded_requirement_missing_when_code_absent_from_rd()
    test_coded_requirement_word_boundary_no_false_match()
    test_requirement_without_code_always_needs_visual_check()
    test_render_report_contains_counts()
    test_triangulation_adapter_only_emits_missing_signals()
    test_triangulation_combines_with_other_room_sources()
    test_duplicate_codes_counted_once()
    test_requirements_with_different_room_label_styles_do_not_break_cross_check()
    test_real_pd_vs_rd_smoke()
    test_general_requirement_gost_token_confirmed_in_rd()
    test_general_requirement_gost_token_missing_in_rd()
    test_general_requirement_quoted_brand_token_tolerates_quote_style()
    test_general_requirement_bare_class_letter_is_not_a_token_alone()
    test_general_requirement_without_any_token_is_flagged_for_manual_review_not_escalated()
    test_render_general_requirement_cross_check_report_shows_counts()
    test_real_pd_vs_rd_general_requirements_smoke()
    print("ALL PASS")
