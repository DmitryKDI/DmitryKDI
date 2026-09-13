import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classification import classify_document, render_stamp_crop_png
from app.llm import LlmConfig
from app.vision import compare_page_pair, compare_text_pair, make_llm_stamp_classifier, render_page_to_data_url

SAMPLE_DIR = Path(
    "/tmp/claude-0/-home-user-DmitryKDI/0870a421-62c2-59a8-8978-c9163f520b16/scratchpad"
)


def test_render_page_to_data_url_real_pdf():
    data_url = render_page_to_data_url(str(SAMPLE_DIR / "rd_floor1.pdf"), 1)
    assert data_url.startswith("data:image/png;base64,")
    assert len(data_url) > 5000  # реальная картинка листа, не заглушка
    print("OK: real page renders to a substantial PNG data URL")


class _FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_stamp_classifier_wired_into_classify_document():
    """Проверяет весь путь: classify_document зовёт vision_stamp_fn только
    когда текст не даёт кода, тот в свою очередь реально шлёт картинку штампа
    через llm.call_llm_json (замокан) и код долетает обратно как
    discipline_code с source='stamp_vision'."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(
            {"content": [{"text": '{"discipline_code": "ОВ", "sheet_name": "План 1-го этажа (вентиляция)"}'}]}
        )

    config = LlmConfig(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-5")
    stamp_classifier = make_llm_stamp_classifier(config)

    with patch("app.llm.httpx.post", side_effect=fake_post):
        result = classify_document(str(SAMPLE_DIR / "rd_floor1.pdf"), "rd_floor1.pdf", vision_stamp_fn=stamp_classifier)

    assert result.discipline_code == "ОВ", result
    assert result.source == "stamp_vision"
    content = captured["json"]["messages"][0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    assert len(image_blocks) == 1, "stamp crop should be sent as exactly one image"
    print("OK: classify_document -> vision stamp classifier -> llm.call_llm_json wired correctly end to end")


def test_compare_page_pair_sends_two_images_with_context():
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(
            {"content": [{"text": '{"significant": [], "checked_total": 1, "significant_total": 0}'}]}
        )

    config = LlmConfig(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-5")
    with patch("app.llm.httpx.post", side_effect=fake_post):
        result = compare_page_pair(
            str(SAMPLE_DIR / "rd_floor1.pdf"), 1,
            str(SAMPLE_DIR / "rd_floor2_heating.pdf"), 1,
            config, context="раздел ОВ, план этажа",
        )

    assert result == {"significant": [], "checked_total": 1, "significant_total": 0}
    content = captured["json"]["messages"][0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    text_block = next(c for c in content if c.get("type") == "text")
    assert len(image_blocks) == 2
    assert "раздел ОВ" in text_block["text"]
    print("OK: page-pair comparison sends both real page images plus classification context in the prompt")


def test_compare_text_pair_sends_text_not_images():
    """Текстовые (не чертёжные) листы сравниваются по тексту — ни одного
    image-блока в запросе быть не должно, это отдельный, более дешёвый путь."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(
            {"content": [{"text":
                '{"significant": [{"label": "A-1", "change": "Класс бетона B25 вместо B30"}],'
                ' "noise_note": "", "checked_total": 1, "significant_total": 1}'}]}
        )

    config = LlmConfig(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-5")
    with patch("app.llm.httpx.post", side_effect=fake_post):
        result = compare_text_pair(
            "Класс бетона по проекту B30", "Класс бетона по факту B25",
            config, context="раздел КР, акт освидетельствования",
        )

    assert result["significant"][0]["change"] == "Класс бетона B25 вместо B30"
    content = captured["json"]["messages"][0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    text_block = next(c for c in content if c.get("type") == "text")
    assert not image_blocks, "no images -> content should carry no image blocks"
    assert "B30" in text_block["text"] and "B25" in text_block["text"]
    assert "раздел КР" in text_block["text"]
    print("OK: text-kind comparison sends no image blocks, both page texts and context in the text block")


def test_known_violations_reach_the_prompt_filtered_by_kind_and_discipline(tmp_path, monkeypatch):
    """Известные нарушения из data/known_violations.json подставляются в
    системный промпт — это единственный способ, которым они влияют на анализ
    (весов модели мы не трогаем). Фильтрация обязательна: примеры для
    чертежей не должны попадать в текстовый промпт и наоборот, иначе модель
    ищет вытяжную вентиляцию в акте освидетельствования.

    Фикстура — синтетические примеры, не содержимое настоящего
    data/known_violations.json: реальный файл сейчас (Приложение Г.24)
    намеренно содержит только универсальные ('*') примеры без привязки к
    разделу, поэтому проверять фильтр по discipline на нём нельзя — тест
    должен быть независим от того, какие разделы там реально заведены."""
    import json

    import app.vision as vision_module

    fixture = tmp_path / "known_violations.json"
    fixture.write_text(json.dumps({"examples": [
        {"discipline": "ОВ", "applies_to": "drawing", "what": "Пример чертежа раздела ОВ",
         "how_to_spot": "маркер-drawing-ov", "severity": "критично"},
        {"discipline": "*", "applies_to": "text", "what": "Пример текста, общий для всех разделов",
         "how_to_spot": "маркер-text-any", "severity": "критично"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(vision_module, "KNOWN_VIOLATIONS_PATH", fixture)

    from app.vision import known_violations_block, load_known_violations, vision_system_prompt

    assert load_known_violations(), "файл примеров должен читаться, иначе промпт молча остаётся общим"

    drawing_ov = known_violations_block("drawing", "ОВ")
    assert "маркер-drawing-ov" in drawing_ov, drawing_ov
    assert "маркер-text-any" not in drawing_ov, "текстовый пример утёк в чертёжный блок"

    text_any = known_violations_block("text", "КР")
    assert "маркер-text-any" in text_any, text_any
    assert "маркер-drawing-ov" not in text_any, "чертёжный пример утёк в текстовый блок"

    # Раздел ЭОМ: специфичных для ОВ примеров быть не должно, общие ('*') — должны.
    drawing_eom = known_violations_block("drawing", "ЭОМ")
    assert "маркер-drawing-ov" not in drawing_eom, "пример раздела ОВ показан для ЭОМ"

    prompt = vision_system_prompt("ОВ")
    assert "маркер-drawing-ov" in prompt
    # Фигурные скобки JSON-шаблона не должны пострадать от .format()
    assert '{"significant"' in prompt, "формат ответа сломан подстановкой примеров"
    print("OK: примеры нарушений попадают в промпт с фильтром по типу листа и разделу")


def test_missing_known_violations_file_does_not_break_prompt(monkeypatch):
    """Отсутствие файла примеров не должно ронять анализ — промпт просто
    остаётся общим, как был до их появления."""
    import app.vision as vision_module
    monkeypatch.setattr(vision_module, "KNOWN_VIOLATIONS_PATH", Path("/nonexistent/known_violations.json"))

    assert vision_module.load_known_violations() == []
    prompt = vision_module.vision_system_prompt("ОВ")
    assert "НАРУШЕНИЯ, УЖЕ ВСТРЕЧАВШИЕСЯ" not in prompt
    assert '{"significant"' in prompt, "промпт должен остаться рабочим без файла примеров"
    print("OK: без файла примеров промпт остаётся корректным, анализ не падает")


def test_compare_page_pair_passes_discipline_into_prompt(tmp_path, monkeypatch):
    """Раздел долетает из main.py до промпта — иначе фильтрация примеров
    существует, но всегда получает None и вырождается в 'все примеры'.

    Фикстура синтетическая — см. пояснение в
    test_known_violations_reach_the_prompt_filtered_by_kind_and_discipline:
    реальный known_violations.json сейчас не содержит ОВ-специфичных
    примеров (Приложение Г.24), тест не должен зависеть от этого факта."""
    import json

    import app.vision as vision_module

    fixture = tmp_path / "known_violations.json"
    fixture.write_text(json.dumps({"examples": [
        {"discipline": "ОВ", "applies_to": "drawing", "what": "Пример чертежа раздела ОВ",
         "how_to_spot": "маркер-drawing-ov", "severity": "критично"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(vision_module, "KNOWN_VIOLATIONS_PATH", fixture)

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse({"content": [{"text": '{"significant": []}'}]})

    config = LlmConfig(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-5")
    with patch("app.llm.httpx.post", side_effect=fake_post):
        compare_page_pair(
            str(SAMPLE_DIR / "rd_floor1.pdf"), 1,
            str(SAMPLE_DIR / "rd_floor2_heating.pdf"), 1,
            config, context="раздел ОВ", discipline="ОВ",
        )

    # У Anthropic системный промпт — отдельное поле верхнего уровня "system",
    # не запись в messages (там только пользовательское сообщение).
    system_prompt = captured["json"]["system"]
    assert "маркер-drawing-ov" in system_prompt, "примеры раздела ОВ не попали в системный промпт"
    print("OK: discipline из main.py доходит до системного промпта сравнения")


def test_compare_page_pair_with_clip_frac_crops_both_sides():
    """Г.55 — зона-кроп (доли листа, найденная visual_prefilter.diff_hot_zone
    до вызова) должна доходить до обеих картинок и до подсказки модели, не
    оставаться параметром, который никуда не передаётся."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse({"content": [{"text": '{"significant": []}'}]})

    config = LlmConfig(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-5")
    full = render_page_to_data_url(str(SAMPLE_DIR / "rd_floor1.pdf"), 1)
    with patch("app.llm.httpx.post", side_effect=fake_post):
        compare_page_pair(
            str(SAMPLE_DIR / "rd_floor1.pdf"), 1,
            str(SAMPLE_DIR / "rd_floor2_heating.pdf"), 1,
            config, clip_frac=(0.1, 0.1, 0.4, 0.4),
        )

    content = captured["json"]["messages"][0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    text_block = next(c for c in content if c.get("type") == "text")
    assert len(image_blocks) == 2
    cropped_data_url = f"data:image/png;base64,{image_blocks[0]['source']['data']}"
    assert cropped_data_url != full, "кроп должен быть другой картинкой, не весь лист"
    assert "зона с найденным визуальным отличием" in text_block["text"]
    print("OK: clip_frac доходит до обеих картинок пары и до текста подсказки модели")


if __name__ == "__main__":
    test_render_page_to_data_url_real_pdf()
    test_stamp_classifier_wired_into_classify_document()
    test_compare_page_pair_sends_two_images_with_context()
    test_compare_page_pair_with_clip_frac_crops_both_sides()
    test_compare_text_pair_sends_text_not_images()
    test_known_violations_reach_the_prompt_filtered_by_kind_and_discipline()
    test_compare_page_pair_passes_discipline_into_prompt()
    print("ALL PASS")
