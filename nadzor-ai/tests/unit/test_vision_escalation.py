"""Эскалация в зрение — проводка без реального провайдера.

Всё в этом файле проверяется офлайн: сборка запроса и контентных блоков —
чистые функции, реальный сетевой вызов не требуется и не выполняется.
"""
from __future__ import annotations

from pathlib import Path

from analysis.llm_layer import _vision_images
from analysis.models import AnalysisContext, ParsedDoc
from analysis.rules.vision_escalation import needs_escalation, render_room_images
from documents.schemas import DocKind, StateKind
from llm_core.ports import CompletionRequest, ImageBlock
from llm_core.providers.remote import _content_blocks

from tests.unit.test_rooms import _doc, _fact

ROOT = Path(__file__).resolve().parents[2]
AS_BUILT_PDF = ROOT / "data/demo/generated/OBJ-001/as_built/aosr_48.pdf"


class _VisionStubProvider:
    """Провайдер, заявляющий поддержку зрения, но никуда не обращающийся."""

    name = "vision-stub"
    model = "stub"
    supports_vision = True


class _TextOnlyStubProvider:
    name = "text-stub"
    model = "stub"
    supports_vision = False


def test_needs_escalation_below_threshold():
    thin = [(_fact("f1", "room", "012", {"room_no": "012"}), _doc("D1", StateKind.PD, []))]
    rich = [(_fact("f1", "text", "", "первый"), _doc("D1", StateKind.PD, [])),
           (_fact("f2", "text", "", "второй"), _doc("D1", StateKind.PD, []))]
    assert needs_escalation(thin, [], min_text_facts=2) is True
    assert needs_escalation(rich, [], min_text_facts=2) is False


def test_render_room_images_reads_real_pdf_and_dedupes_pages():
    doc = ParsedDoc(id="D1", file_id="D1", object_id="OBJ-X", doc_kind=DocKind.AOSR,
                    state_kind=StateKind.AS_BUILT, title="АОСР", path=str(AS_BUILT_PDF))
    fact_a = _fact("f1", "text", "", "текст а", page=1)
    fact_b = _fact("f2", "text", "", "текст б", page=1)      # тот же лист — не дублируем рендер
    images = render_room_images("012", "до", [(fact_a, doc), (fact_b, doc)], dpi=80, max_images=6)
    assert len(images) == 1
    assert images[0].media_type == "image/png"
    assert images[0].data_b64                                # непустая base64-строка
    assert "012" in images[0].label and "АОСР" in images[0].label


def test_vision_images_noop_without_vision_capable_provider():
    """Без ключа/провайдера со зрением эскалация не запускается — тихо и без ошибок."""
    ctx = AnalysisContext(object_id="OBJ-X", run_id="r1", norms=None,
                          rules_config={"llm_layer": {"vision_escalation": {"enabled": True}}},
                          provider=_TextOnlyStubProvider())
    result = _vision_images({"012"}, {"012": []}, {"012": []}, ctx)
    assert result == []


def test_vision_images_disabled_by_config_even_with_vision_provider():
    ctx = AnalysisContext(object_id="OBJ-X", run_id="r1", norms=None,
                          rules_config={"llm_layer": {"vision_escalation": {"enabled": False}}},
                          provider=_VisionStubProvider())
    result = _vision_images({"012"}, {"012": []}, {"012": []}, ctx)
    assert result == []


def test_vision_images_renders_only_thin_candidates():
    doc_before = ParsedDoc(id="PD", file_id="PD", object_id="OBJ-X", doc_kind=DocKind.PD,
                           state_kind=StateKind.PD, title="ПД", path=str(AS_BUILT_PDF))
    doc_after = ParsedDoc(id="ID", file_id="ID", object_id="OBJ-X", doc_kind=DocKind.AOSR,
                          state_kind=StateKind.AS_BUILT, title="ИД", path=str(AS_BUILT_PDF))
    thin_before = [(_fact("f1", "room", "012", {"room_no": "012"}, page=1), doc_before)]
    thin_after = [(_fact("f2", "room", "012", {"room_no": "012"}, page=1), doc_after)]
    rich_before = [(_fact("f3", "text", "", "довольно подробное описание "), doc_before),
                  (_fact("f4", "text", "", "ещё одно подробное описание"), doc_before)]
    rich_after = [(_fact("f5", "text", "", "исполнительное описание"), doc_after),
                 (_fact("f6", "text", "", "второе исполнительное описание"), doc_after)]

    ctx = AnalysisContext(object_id="OBJ-X", run_id="r1", norms=None,
                          rules_config={"llm_layer": {"vision_escalation": {
                              "enabled": True, "min_text_facts": 2, "max_images": 6,
                              "render_dpi": 80}}},
                          provider=_VisionStubProvider())
    images = _vision_images({"012", "140"}, {"012": thin_before, "140": rich_before},
                            {"012": thin_after, "140": rich_after}, ctx)
    labels = " ".join(img.label for img in images)
    assert "012" in labels
    assert "140" not in labels          # у 140 текста достаточно — эскалация не нужна


def test_content_blocks_pure_builder_without_images():
    assert _content_blocks("текст запроса", []) == "текст запроса"


def test_content_blocks_pure_builder_with_images():
    images = [ImageBlock(media_type="image/png", data_b64="AAAA", label="лист 1")]
    blocks = _content_blocks("текст запроса", images)
    assert blocks[0] == {"type": "text", "text": "текст запроса"}
    assert {"type": "text", "text": "[изображение: лист 1]"} in blocks
    image_block = next(b for b in blocks if b["type"] == "image")
    assert image_block["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}


def test_completion_request_carries_images_field():
    """Проводка: запрос умеет нести изображения, не ломая контракт для текстовых провайдеров."""
    req = CompletionRequest(task="semantic_diff", system="", images=[])
    assert req.images == []
    req_with_image = CompletionRequest(
        task="semantic_diff", system="",
        images=[ImageBlock(media_type="image/png", data_b64="AAAA", label="лист 1")])
    assert len(req_with_image.images) == 1


async def test_claude_provider_declares_vision_support():
    from llm_core.providers.remote import ClaudeProvider
    provider = ClaudeProvider({"model": "claude-sonnet-5"})
    assert provider.supports_vision is True


def test_other_providers_do_not_declare_vision_support():
    from llm_core.providers.mock import MockProvider
    from llm_core.providers.remote import GigaChatProvider, OpenAICompatProvider, YandexGPTProvider
    assert MockProvider().supports_vision is False
    assert OpenAICompatProvider({}).supports_vision is False
    assert GigaChatProvider({}).supports_vision is False
    assert YandexGPTProvider({}).supports_vision is False
