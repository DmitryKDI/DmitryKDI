"""Один вызов «требование + лист РД -> статус» на оба вида источника.

Требование ПД бывает записано словами, а бывает показано на листе ПД. Это
меняет только то, какие изображения приложены к вызову, — вопрос к модели,
контракт ответа и его разбор одни и те же. Отдельного конвейера на
«текст -> чертёж» и на «чертёж -> чертёж» не нужно (раздел 14 задания).

Здесь же закреплены два правила ответа: подтверждение при низкой
читаемости листа подтверждением не является (раздел 13), а зоны увеличения
приходят от модели и проверяются кодом, потому что это недоверенные данные.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import vision_page_compare as vpc  # noqa: E402


def _answer(**over):
    base = {"verdict": "confirmed", "comparability": "high",
            "reason": "видно на листе", "where": "оси А-Б"}
    return {**base, **over}


def _patch(monkeypatch, answer, calls):
    monkeypatch.setattr(vpc, "render_page_to_data_url",
                        lambda path, page, clip_frac=None: f"img:{path}:{page}:{clip_frac}")

    def fake_call(config, system, user, images=None, **kw):
        calls.append({"system": system, "user": user, "images": list(images or []),
                      "prompt_version": kw.get("prompt_version")})
        return answer

    monkeypatch.setattr(vpc, "call_llm_json", fake_call)


def test_text_requirement_sends_one_image(monkeypatch):
    calls = []
    _patch(monkeypatch, _answer(), calls)

    out = vpc.check_requirement_on_page("рд.pdf", 2, "Предусмотрено решение X.",
                                        [], object())

    assert len(calls[0]["images"]) == 1, "лишнее изображение к текстовому требованию"
    assert out["verdict"] == "confirmed"
    assert out["requirement_status"] == "appears_compliant"


def test_drawing_requirement_uses_the_same_call_with_pd_sheet(monkeypatch):
    """Тот же вызов, тот же промпт — отличается только вложение."""
    calls = []
    _patch(monkeypatch, _answer(), calls)

    text_only = vpc.check_requirement_on_page("рд.pdf", 2, "Решение X.", [], object())
    with_pd = vpc.check_requirement_on_page("рд.pdf", 2, "Решение X.", [], object(),
                                            pd_images=["img:пд.pdf:5:None"])

    assert calls[0]["system"] == calls[1]["system"], "промпт обязан быть общим"
    assert calls[0]["prompt_version"] == calls[1]["prompt_version"]
    assert len(calls[1]["images"]) == 2
    assert calls[1]["images"][0] == "img:пд.pdf:5:None", "лист ПД идёт первым"
    assert text_only["requirement_status"] == with_pd["requirement_status"]
    assert "Первое изображение — лист ПД" in calls[1]["user"]
    assert "Первое изображение" not in calls[0]["user"]


def test_confirmed_on_unreadable_sheet_is_downgraded(monkeypatch):
    """«Выполнено» при низкой читаемости — это не выполнено, а не видно."""
    calls = []
    _patch(monkeypatch, _answer(comparability="low"), calls)

    out = vpc.check_requirement_on_page("рд.pdf", 2, "Решение X.", [], object())

    assert out["verdict"] == "unclear"
    assert out["requirement_status"] == "unclear"
    assert "низкой" in out["reason"], out["reason"]


def test_absent_is_not_upgraded_or_hidden(monkeypatch):
    """Обратное направление не трогаем: «не найдено» остаётся кандидатом."""
    calls = []
    _patch(monkeypatch, _answer(verdict="absent", comparability="low"), calls)

    out = vpc.check_requirement_on_page("рд.pdf", 2, "Решение X.", [], object())

    assert out["verdict"] == "absent"
    assert out["requirement_status"] == "candidate_difference"


def test_model_regions_are_validated_not_trusted():
    """Координаты от модели — недоверенные данные."""
    regions = vpc.normalize_regions([
        {"reason": "годная", "rd_bbox_norm": [0.1, 0.2, 0.4, 0.5], "priority": "high"},
        {"reason": "углы наоборот", "rd_bbox_norm": [0.9, 0.9, 0.2, 0.2]},
        {"reason": "схлопнута", "rd_bbox_norm": [0.3, 0.3, 0.3, 0.3]},
        {"reason": "за листом", "rd_bbox_norm": [-1, -1, 5, 5]},
        {"reason": "не число", "rd_bbox_norm": ["a", 0, 1, 1]},
        {"reason": "не тот размер", "rd_bbox_norm": [0.1, 0.2]},
    ])

    assert [r["reason"] for r in regions] == ["годная", "углы наоборот", "за листом"]
    assert regions[0]["rd_bbox_norm"] == (0.1, 0.2, 0.4, 0.5)
    assert regions[1]["rd_bbox_norm"] == (0.2, 0.2, 0.9, 0.9), "углы развёрнуты, не отброшены"
    assert regions[2]["rd_bbox_norm"] == (0.0, 0.0, 1.0, 1.0), "обрезано по листу"
    assert regions[0]["priority"] == "high"
    assert regions[1]["priority"] == "medium", "приоритет по умолчанию"


def test_provider_error_is_unclear_never_compliant(monkeypatch):
    """Сбой вызова не превращается в «выполнено» (раздел 13 задания)."""
    monkeypatch.setattr(vpc, "render_page_to_data_url",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("нет связи")))

    out = vpc.check_requirement_on_page("рд.pdf", 2, "Решение X.", [], object())

    assert out["verdict"] == "unclear"
    assert out["requirement_status"] == "unclear"
    assert out["error"] is True
    assert out["comparability"] == "low"
