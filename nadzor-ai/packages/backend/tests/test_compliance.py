"""Сверка «выполнено ли в РД то, что требует ПД» (Г.96).

Указание пользователя и его же предупреждение: «имей в виду, что может быть
много ложных срабатываний, ведь в РД меньше текста; мы смотрим по списку
требований ПД и то, что в РД написано. Если в ПД указано, что в таких-то
местах делаем так, то система уже в РД смотрит по чертежам (если нет
требования в тексте), так ли сделано».

Отсюда главное свойство модуля, которое здесь и проверяется: **отсутствие
подтверждения НЕ называется нарушением ни при каких обстоятельствах**. В
рабочей документации текста мало по природе (Г.8), поэтому «не нашли в
тексте» — ожидаемое состояние, а не улика. Итог для инспектора — три
статуса: подтверждено, требует проверки на листе, не проверялось.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compliance import (  # noqa: E402
    STATUS_CONFIRMED,
    STATUS_NEEDS_CHECK,
    STATUS_NOT_CHECKED,
    check_compliance,
    render_compliance_report,
)
from app.requirement_registry import Requirement  # noqa: E402

# Путь фиктивный: зрение в этом тесте замокано, файл не открывается.
FAKE_RD_PATH = "не-существующий-файл-рд.pdf"


def _req(sentence: str, rooms=None, summary="", page=1) -> Requirement:
    return Requirement(rooms=rooms or [], page=page, sentence=sentence, code=None,
                       document="пд.pdf", section="ОВ", summary=summary or sentence[:60])


def test_token_found_in_rd_text_confirms_without_spending_a_call():
    """Первая ступень — самая дешёвая: нормативный номер или марка из
    требования ищется прямо в тексте РД. Нашлась — вызов модели не нужен."""
    reqs = [_req("Регистры из бесшовных труб по ГОСТ 8732-78.")]
    rd_text = [{"page": 4,
                "text": "Трубы стальные бесшовные ГОСТ 8732-78, поставка по спецификации."}]
    calls = []

    result = check_compliance(reqs, rd_text_facts=rd_text, rd_sources=[],
                              config=None, llm_verify=lambda *a, **kw: calls.append(1) or [])

    assert len(result.items) == 1
    assert result.items[0].status == STATUS_CONFIRMED
    assert "ГОСТ 8732-78" in result.items[0].evidence
    assert not calls, "при совпадении токена модель не вызывается"


def test_absent_in_text_is_never_called_a_violation():
    """Главная защита от ложных срабатываний. Модель сказала «в тексте РД
    этого нет» — это НЕ нарушение: в РД текста мало по природе."""
    reqs = [_req("Высота установки приборов на путях эвакуации не менее 2,2 м.")]
    result = check_compliance(
        reqs, rd_text_facts=[{"page": 1, "text": "Общие данные."}], rd_sources=[], config=object(),
        llm_verify=lambda *a, **kw: [{"sentence": reqs[0].sentence, "verdict": "absent",
                                      "reason": "в тексте не упомянуто"}],
    )
    item = result.items[0]
    assert item.status == STATUS_NEEDS_CHECK
    assert "наруш" not in item.detail.lower(), item.detail
    # В отчёте слово «нарушение» допустимо ровно в одном месте — в оговорке
    # «это НЕ является заключением о нарушениях». Ни один пункт списка так
    # называться не должен.
    report = render_compliance_report(result)
    body = report.split("--- ")[1] if "--- " in report else ""
    assert "наруш" not in body.lower(), body


def test_visual_step_runs_only_for_requirements_tied_to_rooms():
    """Зрение — дорогая ступень, поэтому идёт только туда, где есть за что
    зацепиться: номер помещения из требования. Требование к объекту целиком
    визуально проверять не по чему, и это честно помечается."""
    tied = _req("В помещениях 140 и 141 предусмотрены местные отсосы.", rooms=["140", "141"])
    general = _req("Все трубопроводы изолируются.")
    seen = []

    def fake_vision(pdf_path, page_no, requirement_text, rooms, config, **kw):
        seen.append(rooms)
        return {"verdict": "confirmed", "reason": "видно на листе", "where": "лист 7"}

    result = check_compliance(
        [tied, general], rd_text_facts=[{"page": 1, "text": "Общие данные."}],
        rd_sources=[(FAKE_RD_PATH, "рд.pdf")], config=object(),
        llm_verify=lambda *a, **kw: [
            {"sentence": tied.sentence, "verdict": "absent", "reason": "нет в тексте"},
            {"sentence": general.sentence, "verdict": "absent", "reason": "нет в тексте"},
        ],
        vision_check=fake_vision, candidate_pages=lambda rooms, sources: [(FAKE_RD_PATH, 7)],
    )
    by_sentence = {i.requirement.sentence: i for i in result.items}

    assert seen == [["140", "141"]], "зрение вызвано только для требования с помещениями"
    assert by_sentence[tied.sentence].status == STATUS_CONFIRMED
    assert by_sentence[general.sentence].status == STATUS_NEEDS_CHECK
    assert "помещени" in by_sentence[general.sentence].detail.lower()


def test_without_llm_key_nothing_is_declared_confirmed_or_missing():
    """Без ключа доступна только токен-сверка. Всё остальное — «не
    проверялось», а не «не найдено»: это разные вещи (Г.10)."""
    reqs = [_req("Экраны из негорючих материалов.")]
    result = check_compliance(reqs, rd_text_facts=[{"page": 1, "text": "Ничего похожего."}],
                              rd_sources=[], config=None)
    assert result.items[0].status == STATUS_NOT_CHECKED
    assert "ключ" in result.items[0].detail.lower()


def test_report_counts_every_requirement_and_explains_the_statuses():
    """Отчёт обязан сходиться: сумма по статусам равна числу требований, и
    каждый статус объяснён — иначе инспектор не поймёт, что с ним делать."""
    reqs = [_req("Регистры по ГОСТ 8732-78."), _req("Прочее требование.")]
    result = check_compliance(reqs, rd_text_facts=[{"page": 1, "text": "ГОСТ 8732-78"}],
                              rd_sources=[], config=None)
    text = render_compliance_report(result)

    total = sum(result.counts.values())
    assert total == len(reqs) == len(result.items)
    for status in (STATUS_CONFIRMED, STATUS_NEEDS_CHECK, STATUS_NOT_CHECKED):
        assert status in text, f"в отчёте нет счётчика статуса «{status}»"
    assert "не является заключением" in text.lower(), "отчёт — гипотезы, не вердикты (Б.6)"
    assert "решение принимает инспектор" in text.lower()


def test_llm_failure_does_not_become_a_missing_requirement():
    """Сбой вызова — «не проверялось», а не «в РД нет». Ровно та подмена,
    что стоила проекту трёх раундов правок промпта (Г.77)."""
    reqs = [_req("Какое-то требование.")]

    def boom(*a, **kw):
        raise ConnectionError("сеть недоступна")

    result = check_compliance(reqs, rd_text_facts=[{"page": 1, "text": "текст"}],
                              rd_sources=[], config=object(), llm_verify=boom)
    item = result.items[0]
    assert item.status == STATUS_NOT_CHECKED
    assert "сеть недоступна" in item.detail
