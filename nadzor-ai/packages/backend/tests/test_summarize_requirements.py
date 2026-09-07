"""Тесты стадии 1 — разбора проектной документации (Г.86).

Стадия самодостаточна: РД не требует и о ней не знает. Здесь проверяется
её контракт — порядок шагов, поведение без ключа ЛЛМ и явный отказ при
ненайденном файле.
"""
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.summarize_requirements as sr  # noqa: E402
from app.requirement_registry import Requirement  # noqa: E402


def _make_pdf(path: Path, text: str = "Экраны должны быть негорючими.") -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_main_exits_with_clear_error_on_missing_pd_file(monkeypatch):
    """Г.82 — несуществующий путь раньше давал «чистый» отчёт из нулей с
    exit=0, неотличимый от честного «в документе нет требований»."""
    monkeypatch.setattr(sys, "argv", [
        "summarize_requirements.py", "--pd", "/nonexistent/does-not-exist.pdf", "--api-key", "FAKE",
    ])
    with pytest.raises(SystemExit) as exc_info:
        sr.main()
    assert exc_info.value.code != 0
    assert "не найден" in str(exc_info.value.code)
    print("OK: несуществующий --pd файл — явная ошибка, не тихий нулевой отчёт")


def test_runs_without_key_on_regex_path_instead_of_failing(monkeypatch, capsys, tmp_path):
    """Г.86 — ОБРАТНОЕ прежнему поведению, намеренно. Раньше отсутствие
    ключа было ошибкой с выходом. Теперь стадия обязана доработать: без
    ключа идёт regex-путь, сводка выходит сырой, но программа не встаёт —
    прямое требование пользователя «программа не должна тупануть»."""
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    monkeypatch.setattr(sys, "argv", ["summarize_requirements.py", "--pd", str(pdf_path)])

    sr.main()  # не должно бросить SystemExit

    out = capsys.readouterr().out
    assert "regex-путь" in out, "пользователь должен видеть, что сводка сырая"
    assert "СЫРОЙ" in out
    print("OK: без ключа стадия отрабатывает regex-путём, а не падает")


def test_api_key_from_env_var_reaches_llm_config(monkeypatch, tmp_path):
    """Ключ из окружения должен доезжать до конфигурации ЛЛМ — иначе
    vision-keys.env и GIGACHAT_CA_BUNDLE не действуют (Г.82)."""
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "FAKE_ENV_KEY")
    monkeypatch.setattr(sys, "argv", ["summarize_requirements.py", "--pd", str(pdf_path)])

    seen: list[str] = []
    monkeypatch.setattr(sr, "_extract_requirements_llm_visible",
                        lambda facts, cfg, emit: seen.append(cfg.api_key) or [])
    sr.main()

    assert seen == ["FAKE_ENV_KEY"]
    print("OK: GIGACHAT_CREDENTIALS из окружения подхватывается без --api-key")


def test_step_order_matches_the_specified_pipeline(monkeypatch, capsys, tmp_path):
    """Г.86 — порядок задан пользователем: комплект → факты → требования →
    сводка → реестры. Требования и сводка идут ДО реестров, не после."""
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    monkeypatch.setattr(sys, "argv", ["summarize_requirements.py", "--pd", str(pdf_path), "--api-key", "K"])
    monkeypatch.setattr(sr, "_extract_requirements_llm_visible", lambda facts, cfg, emit: [])
    sr.main()

    out = capsys.readouterr().out
    order = [out.index(s) for s in ("Шаг 1. Комплект", "Шаг 2. Текст",
                                    "Шаг 3. Требования", "Шаг 4. Сводка", "Шаги 5-6")]
    assert order == sorted(order), f"шаги идут не по порядку: {order}"
    print("OK: порядок шагов совпадает с заданным")


def test_unconnected_steps_are_announced_not_silently_skipped(monkeypatch, capsys, tmp_path):
    """Г.10 — реестры и графика к этой стадии ещё не подключены; молчать об
    этом нельзя, иначе отсутствие раздела в выводе читается как «там пусто»."""
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    monkeypatch.setattr(sys, "argv", ["summarize_requirements.py", "--pd", str(pdf_path), "--api-key", "K"])
    monkeypatch.setattr(sr, "_extract_requirements_llm_visible", lambda facts, cfg, emit: [])
    sr.main()

    assert "не подключены" in capsys.readouterr().out
    print("OK: неподключённые шаги названы явно, а не пропущены молча")


def test_summary_groups_by_section_and_shows_page():
    """Г.86 — «требования должны быть уже привязаны к разделам... инспектор
    просто видит, на какой странице требование»."""
    reqs = [
        Requirement(rooms=[], page=12, sentence="Отходы вывозить по договору.",
                    document="Том ООС8.1.pdf", section="ООС"),
        Requirement(rooms=["140"], page=3, sentence="В пом. 140 предусмотреть вытяжку.",
                    document="Том ОВ5.4.pdf", section="ОВ"),
    ]
    text = sr.render_summary(reqs)
    assert "[ООС]" in text and "[ОВ]" in text
    assert "Том ООС8.1.pdf" in text and "Том ОВ5.4.pdf" in text
    assert "стр.12" in text and "стр.3" in text
    assert "пом. 140" in text
    print("OK: сводка сгруппирована по разделам, у каждого требования есть страница")


def test_summary_says_so_when_nothing_extracted():
    assert "не извлечено" in sr.render_summary([])
    print("OK: пустой результат назван прямо, а не показан пустым разделом")
