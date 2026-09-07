import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.summarize_requirements as sr  # noqa: E402


def _make_pdf(path: Path, text: str = "Экраны должны быть негорючими.") -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_main_exits_with_clear_error_on_missing_pd_file(monkeypatch, capsys):
    """Г.82 (независимый аудит Opus, критическая находка №1) — раньше
    несуществующий --pd путь тихо давал "чистый" отчёт из нулей с exit=0,
    неотличимый от честного "в документе нет требований". Теперь должен
    падать явной ошибкой ДО любого обращения к ЛЛМ."""
    monkeypatch.setattr(sys, "argv", [
        "summarize_requirements.py",
        "--pd", "/nonexistent/does-not-exist.pdf",
        "--api-key", "FAKEKEY",
    ])
    with pytest.raises(SystemExit) as exc_info:
        sr.main()
    assert exc_info.value.code != 0
    assert "не найден" in str(exc_info.value.code) or "ОШИБКА" in str(exc_info.value.code)
    print("OK: несуществующий --pd файл — явная ошибка, не тихий нулевой отчёт")


def test_main_exits_with_clear_error_when_no_api_key_anywhere(monkeypatch, tmp_path):
    """Г.82 (находка №2) — без ключа ни аргументом, ни переменной окружения,
    скрипт должен явно остановиться, а не пытаться звать ЛЛМ без ключа."""
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "summarize_requirements.py",
        "--pd", str(pdf_path),
        "--provider", "gigachat",
    ])
    with pytest.raises(SystemExit) as exc_info:
        sr.main()
    assert "ключ" in str(exc_info.value.code).lower()
    print("OK: нет ключа ни аргументом, ни в окружении — явная ошибка")


def test_main_reads_api_key_from_env_var_fallback(monkeypatch, tmp_path):
    """Г.82 (находка №2) — CURRENT-TASK.md/vision-keys.env ожидают, что
    GIGACHAT_CREDENTIALS подхватывается без --api-key, как в registry_diff.py
    (_PROVIDER_ENV_KEY). Сетевые вызовы замоканы (не бьём реальный GigaChat в
    тесте) — проверяем только то, что ключ реально дошёл до LlmConfig."""
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "FAKE_ENV_KEY")
    monkeypatch.setattr(sys, "argv", [
        "summarize_requirements.py",
        "--pd", str(pdf_path),
        "--provider", "gigachat",
    ])

    seen_keys: list[str] = []

    def fake_extract(pd_text_facts, llm_config, _emit):
        seen_keys.append(llm_config.api_key)
        return []

    def fake_emit_general(general_requirements, llm_config, _emit):
        seen_keys.append(llm_config.api_key)

    monkeypatch.setattr(sr, "_extract_requirements_llm_visible", fake_extract)
    monkeypatch.setattr(sr, "_emit_general_requirements", fake_emit_general)

    sr.main()

    assert seen_keys == ["FAKE_ENV_KEY"], "форма 1/2 по умолчанию не должна запускаться (Г.83)"
    print("OK: GIGACHAT_CREDENTIALS из окружения подхватывается без --api-key")


def _run_main_capturing(monkeypatch, capsys, tmp_path, extra_argv=()):
    """Прогон main() с замоканными ЛЛМ-шагами — возвращает (вывод, порядок вызовов)."""
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    monkeypatch.setattr(sys, "argv", [
        "summarize_requirements.py", "--pd", str(pdf_path),
        "--provider", "gigachat", "--api-key", "FAKEKEY", *extra_argv,
    ])
    calls: list[str] = []

    def fake_extract(pd_text_facts, llm_config, _emit):
        calls.append("форма 1/2")
        return []

    def fake_emit_general(general_requirements, llm_config, _emit):
        calls.append("форма 3")

    monkeypatch.setattr(sr, "_extract_requirements_llm_visible", fake_extract)
    monkeypatch.setattr(sr, "_emit_general_requirements", fake_emit_general)
    sr.main()
    return capsys.readouterr().out, calls


def test_text_requirements_run_first_and_form_1_2_is_off_by_default(monkeypatch, capsys, tmp_path):
    """Г.83 — прямое указание пользователя: приоритет у извлечения требований
    ИЗ ТЕКСТА. Форма 1/2 (привязка к номеру помещения) не должна запускаться
    по умолчанию: она питает автоматическую сверку с РД по номеру (Г.33/46),
    которой на этом этапе нет, стоит десятки вызовов ЛЛМ против пяти у формы 3
    и на ООС/ПОС/ПБ по своей природе даёт 0 — а программа последовательна,
    поэтому этот шаг выбирал лимит частоты ДО полезного (429 на КР/НВ, Г.78)."""
    out, calls = _run_main_capturing(monkeypatch, capsys, tmp_path)
    assert calls == ["форма 3"], f"по умолчанию должна идти только форма 3, а шло: {calls}"
    assert out.index("из текста (форма 3") < out.index("Форма 1/2"), "требования из текста — первыми"
    assert "НЕ запускалась" in out, "пропуск шага должен быть виден явно (Г.10), не молча"
    assert "--with-room-requirements" in out, "должно быть сказано, как включить форму 1/2"
    print("OK: по умолчанию идёт только извлечение требований из текста, форма 1/2 явно отключена")


def test_form_1_2_runs_after_text_requirements_when_flag_given(monkeypatch, capsys, tmp_path):
    """С флагом форма 1/2 возвращается — но ПОСЛЕ требований из текста,
    а не перед ними: приоритет не меняется от включения дополнительного шага."""
    out, calls = _run_main_capturing(
        monkeypatch, capsys, tmp_path, extra_argv=("--with-room-requirements",),
    )
    assert calls == ["форма 3", "форма 1/2"], f"порядок должен быть текст→помещения, а был: {calls}"
    assert "Дополнительно: требования с привязкой к помещению" in out
    print("OK: с флагом форма 1/2 прогоняется, но после требований из текста")
