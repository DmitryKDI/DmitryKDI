"""LLM-фильтр общего каталога требований (форма 3, Г.47) — второй проход
поверх уже извлечённых regex-кандидатов (Приложение Г.69 CLAUDE.md).

Прямая идея пользователя: после того как форма 3
(`requirement_registry.extract_general_requirements`) регулярками находит
кандидатов — широко и осознанно шумно, см. докстринг `requirement_registry.py`
(«шум — приемлемая цена за полноту») — отправить эти кандидаты отдельным
вызовом ЛЛМ, чтобы модель приняла ОКОНЧАТЕЛЬНОЕ решение по каждому
предложению: настоящее это текстовое требование к объекту, или шум
(описание существующего состояния, голая ссылка на норму без содержания,
обрывок таблицы). Мотивация пользователя прямая: именно текстовые
требования ПД — первичный вход, на основе которого строится остальная
документация (чертежи, спецификации) и вся дальнейшая сверка, поэтому
качество этого каталога важнее его размера.

Раздело-независимо ПО КОНСТРУКЦИИ, не только по описанию: модель судит по
смыслу предложения на естественном языке (то же самое умеет делать для
прозы любого раздела — конструктив, электрика, слаботочка, не только ОВ),
как и `requirement_llm_extract.py`/`verdict_synthesis.py`. Это НЕ замена
`requirement_llm_extract.py` (тот извлекает требования С НУЛЯ ЛЛМ-вызовом
по тексту страницы, форма 1/2, Г.36/Г.42) и не замена самой формы 3 —
только дополнительный проход-фильтр НАД уже извлечённым regex-каталогом,
включаемый только при наличии ключа провайдера (без ключа — форма 3
печатается как раньше, `requirement_registry.render_general_requirements_summary`,
RUN-NO-LLM.bat не теряет функциональность).

Батчинг, не по одному вызову на предложение (в отличие от
`verdict_synthesis.synthesize_verdict`, один вызов на ключ): реальный
каталог формы 3 — сотни предложений на один том (реально замерено на
«Школа-600», Г.68: 195-176 уникальных формулировок на раздел), вызов на
каждое был бы дорог и медленен; здесь — пачками по числу кандидатов
(`batch_size`), тот же принцип, что `_chunk_text_facts` уже применяет в
`requirement_llm_extract.py`, только по количеству предложений, а не
странице. Ответ ЛЛМ адресуется по ИНДЕКСУ внутри пачки, не по тексту
предложения — надёжнее при похожих или дублирующихся формулировках.

Ничего не выбрасывается молча (Г.10): `render_general_requirements_summary_llm_filtered`
показывает отсеянные пункты отдельным видимым списком внизу («шум, для
проверки инспектором»), с обоснованием модели по каждому — инспектор
может проверить решение, а не просто довериться ему. Сбой одной пачки
(сеть, провайдер) не роняет весь фильтр и не выбрасывает её кандидатов —
они остаются в списке как есть (`is_requirement=True`, честная пометка
«ЛЛМ не дала вердикт»), тот же принцип «сбой — не значит пусто», что уже
применяется в `requirement_llm_extract.extract_requirements_llm`."""
from __future__ import annotations

from dataclasses import dataclass

from .llm import LlmConfig, call_llm_json
from .requirement_registry import Requirement
from .vision import UNTRUSTED_INPUT_RULE

_FILTER_SYSTEM_PROMPT = f"""\
Ты помогаешь инспектору государственного строительного надзора очистить
черновой список кандидатов в требования, извлечённый простым текстовым
поиском (regex) из прозы проектной документации ЛЮБОГО раздела (не только
инженерных систем — это может быть конструктив, электрика, слаботочка,
архитектура, любой раздел).

Черновой поиск сознательно широкий и содержит шум: описательные
предложения без обязывающего смысла, голые ссылки на нормативный
документ без другого содержания, обрывки таблиц, случайно попавшие
фрагменты. Тебе показан пронумерованный список кандидатов — по каждому
номеру реши: это настоящее текстовое ТРЕБОВАНИЕ (обязывает что-то
предусмотреть, установить, выполнить, обеспечить — для помещения, зоны,
системы или объекта в целом), или ШУМ (просто описание существующего
состояния, ссылка на норму без другого содержания, обрывок таблицы,
случайный фрагмент без обязывающего смысла).

{UNTRUSTED_INPUT_RULE}

Отвечай только JSON без пояснений вне JSON:
{{"verdicts": [
  {{"index": <int, номер кандидата из списка>,
   "is_requirement": true | false,
   "reasoning": "1 короткая фраза почему"}}
]}}
Ответь по КАЖДОМУ номеру из списка, ни один не пропускай."""


@dataclass(frozen=True)
class RequirementVerdict:
    """Решение ЛЛМ по одному кандидату формы 3."""
    requirement: Requirement
    is_requirement: bool
    reasoning: str


def _chunk_requirements(requirements: list[Requirement], batch_size: int) -> list[list[Requirement]]:
    return [requirements[i:i + batch_size] for i in range(0, len(requirements), batch_size)]


def _render_batch(batch: list[Requirement]) -> str:
    body = "\n".join(f"{i}. «{r.sentence}»" for i, r in enumerate(batch, 1))
    return f"<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\nСписок кандидатов:\n{body}\n</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>"


def classify_general_requirements(
    requirements: list[Requirement],
    config: LlmConfig,
    batch_size: int = 20,
    timeout: float = 90.0,
) -> list[RequirementVerdict]:
    """Классифицирует ВЕСЬ список кандидатов формы 3 пачками по
    `batch_size`. Порядок сохраняется (индекс в результате соответствует
    порядку во входном списке, не порядку ответа модели)."""
    out: list[RequirementVerdict] = []
    for batch in _chunk_requirements(requirements, batch_size):
        user_text = _render_batch(batch)
        try:
            result = call_llm_json(config, _FILTER_SYSTEM_PROMPT, user_text, timeout=timeout)
        except Exception:  # noqa: BLE001 — сбой одной пачки не должен ронять весь фильтр
            result = None
        verdicts_by_index: dict[int, dict] = {}
        if result:
            for item in result.get("verdicts", []):
                idx = item.get("index")
                if isinstance(idx, int):
                    verdicts_by_index[idx] = item
        for i, req in enumerate(batch, 1):
            item = verdicts_by_index.get(i)
            if item is None:
                out.append(RequirementVerdict(
                    requirement=req, is_requirement=True,
                    reasoning="ЛЛМ не дала вердикт по этому пункту (сбой вызова или "
                              "пропуск в ответе) — оставлено как требование, не выброшено молча",
                ))
                continue
            out.append(RequirementVerdict(
                requirement=req,
                is_requirement=bool(item.get("is_requirement", True)),
                reasoning=str(item.get("reasoning") or ""),
            ))
    return out


def render_general_requirements_summary_llm_filtered(verdicts: list[RequirementVerdict]) -> str:
    """Печатный каталог формы 3 ПОСЛЕ ЛЛМ-фильтра (Г.69) — заменяет собой
    `requirement_registry.render_general_requirements_summary` в выводе,
    когда есть ключ провайдера (без ключа печатается по-старому, сырой
    regex-каталог). Отсеянное как шум показано отдельным видимым списком
    внизу с обоснованием модели, не выброшено молча (Г.10) — инспектор
    может проверить решение."""
    kept = [v for v in verdicts if v.is_requirement]
    dropped = [v for v in verdicts if not v.is_requirement]
    lines = [
        f"=== Требования ПД после ЛЛМ-фильтра (Г.69, форма 3) — "
        f"кандидатов: {len(verdicts)}, оставлено: {len(kept)}, отсеяно как шум: {len(dropped)} ===",
    ]
    for i, v in enumerate(kept, 1):
        req = v.requirement
        rooms = f" — помещения: {', '.join(req.rooms)}" if req.rooms else ""
        lines.append(f"{i}. стр.{req.page}{rooms}")
        lines.append(f"   «{req.sentence}»")
    if dropped:
        lines.append(f"\n--- отсеяно как шум ({len(dropped)}), для проверки инспектором ---")
        for v in dropped:
            lines.append(f"  стр.{v.requirement.page}: «{v.requirement.sentence}» — {v.reasoning}")
    return "\n".join(lines)
