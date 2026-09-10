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


def test_token_found_without_model_does_not_confirm_whole_requirement():
    """Старый тест закреплял ложное подтверждение по отдельному обозначению."""
    reqs = [_req("Регистры из бесшовных труб по ГОСТ 8732-78.")]
    rd_text = [{"page": 4,
                "text": "Трубы стальные бесшовные ГОСТ 8732-78, поставка по спецификации."}]
    calls = []

    result = check_compliance(reqs, rd_text_facts=rd_text, rd_sources=[],
                              config=None, llm_verify=lambda *a, **kw: calls.append(1) or [])

    assert len(result.items) == 1
    assert result.items[0].status == STATUS_NOT_CHECKED
    assert not result.items[0].evidence
    assert not calls, "без ключа модель не вызывается"
    print("OK: совпадение токена без модели не подтверждает требование")


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


def test_candidate_sheets_are_reported_even_when_vision_is_not_available():
    """Г.106 — «листы подобраны, но смотреть их нечем» и «подходящих листов не
    найдено» это разные состояния.

    Найдено прогоном на реальном комплекте: подбор листов был завязан на
    наличие зрения, и прогон без него докладывал «подходящих листов РД не
    найдено». Это выдавало НЕВЫПОЛНЕННЫЙ шаг за отрицательный результат
    поиска (Г.10) и заодно теряло главное, что нужно инспектору, — куда
    смотреть.
    """
    req = Requirement(rooms=["001"], page=5, sentence="Требование к помещению 001.")
    result = check_compliance(
        requirements=[req],
        rd_text_facts=[{"page": 1, "text": "текст без подтверждения"}],
        rd_sources=[("/tmp/rd.pdf", "rd.pdf")],  # noqa: S108 — путь в тест не открывается
        # Ключ есть и смысловая сверка отработала — не подтвердила; зрения
        # при этом нет. Именно эта комбинация и терялась.
        config=object(),
        llm_verify=lambda reqs, facts, cfg: [],
        vision_check=None,
        candidate_pages=lambda rooms, sources: [("/tmp/rd.pdf", 12)],  # noqa: S108
    )
    item = result.items[0]
    assert item.status == STATUS_NEEDS_CHECK
    assert item.pages_to_check == [("/tmp/rd.pdf", 12)], "лист назван"  # noqa: S108
    assert "просмотр изображения не выполнялся" in item.detail
    assert "не найдено" not in item.detail, "невыполненный шаг не выдаётся за пустой поиск"


def test_vision_is_not_told_the_guessed_rooms():
    """Г.106 — граница между «источником листов» и «привязкой».

    Подсказка по названию решает, КАКОЙ лист открыть, но в вопрос к модели
    не входит: модели уходит `req.rooms` (номера, названные документом), и у
    требования без номеров это пустой список. Иначе догадка «похоже, речь об
    этих помещениях» пришла бы к модели утверждением, и её «подтверждено»
    относилось бы к нашей же гипотезе, а не к требованию ПД.
    """
    req = Requirement(rooms=[], rooms_by_name=["147", "198"], page=15,
                      sentence="Воздуховоды вытяжных шкафов — коррозионностойкие.")
    asked: list[list[str]] = []

    def vision(pdf_path, page_no, sentence, rooms, config):
        asked.append(list(rooms))
        return {"verdict": "unclear", "reason": "не разобрать"}

    result = check_compliance(
        requirements=[req], rd_text_facts=[{"text": "лист без текста"}],
        rd_sources=[(FAKE_RD_PATH, "рд.pdf")], config=object(),
        llm_verify=lambda reqs, facts, cfg: [],
        vision_check=vision,
        candidate_pages=lambda rooms, sources: [(FAKE_RD_PATH, 19)],
    )
    assert asked == [[]], f"догадка ушла в вопрос к модели: {asked}"
    assert result.items[0].pages_to_check == [(FAKE_RD_PATH, 19)], "лист подобран по подсказке"
    print("OK: подсказка выбирает лист, но модели как факт не передаётся")


def test_absent_semantic_step_is_reported_as_not_run():
    """Г.107 — ключ задан, а смысловая сверка не подключена: это НЕ «модель
    прочитала и не подтвердила», а невыполненный шаг. Молчание здесь — то
    же самое, что три раунда правок промпта против сорванной связи (Г.77).
    """
    req = Requirement(rooms=[], page=7, sentence="Требование без обозначения.")
    result = check_compliance(
        requirements=[req], rd_text_facts=[{"text": "текста РД мало"}],
        rd_sources=[(FAKE_RD_PATH, "рд.pdf")], config=object(),
        llm_verify=None, vision_check=None, candidate_pages=None,
    )
    assert any("смысловая сверка" in x for x in result.not_run), result.not_run
    print("OK: неподключённая смысловая сверка названа невыполненным шагом")
