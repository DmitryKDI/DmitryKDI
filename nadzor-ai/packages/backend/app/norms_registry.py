"""Перечень нормативных документов тома и привязка к нему требований (Г.101).

Указание пользователя: «некоторые данные, которые выделяются, например
параметры (расчёты или параметры воздуха), должна связывать с нормой, и чтобы
в выводе было сказано, с какой НД это соответствует. В документах часто есть
список норм, на основе которых выполнен проект».

## Откуда берутся нормативы — и только оттуда

Из САМОГО документа. Проектная документация по ГОСТ Р 21.1101 содержит
раздел «Перечень использованных нормативных документов», и это единственный
допустимый источник: перечень объявлен проектировщиком, значит проект обязан
ему соответствовать, и инспектор может на него сослаться.

Модель НЕ придумывает нормативы и НЕ выбирает их из своей памяти. Она
выбирает из закрытого списка, прочитанного из этого тома, либо честно
отвечает «не могу сопоставить». Это прямое следствие правила 7 раздела 0:
выдуманный реквизит хуже отсутствующего, а номер норматива — ровно тот
реквизит, который модель выдумывает охотнее всего.

**Пункты нормативов не извлекаются вообще.** Ссылка даётся на ДОКУМЕНТ
целиком, а не на пункт внутри него. Пункт нельзя проверить, не
имея текста норматива, а его у системы нет; названный наугад пункт выглядит
как точная ссылка и потому опаснее её отсутствия (раздел 0, правило 3 требует
ссылку на пункт для ВЫВОДА О НАРУШЕНИИ — здесь же не вывод, а подсказка, к
какому нормативу относится параметр).

## Два способа привязки, и они не равны по надёжности

`названа в требовании` — обозначение норматива стоит прямо в тексте
требования, рядом с самим решением. Проверяется поиском подстроки, вызова
модели не требует и ошибиться не может.

`сопоставлено по смыслу` — параметр норматива не называет («расчётная
температура наружного воздуха -26 °C»), и связь предлагает модель, выбирая из
перечня. Это ГИПОТЕЗА, и в отчёте она помечена именно так: разные способы
привязки, слитые в одну пометку, превращают предположение в утверждение.

## Различитель «перечень против оглавления»

Заголовок «Перечень использованных нормативных документов» встречается
дважды: в содержании тома и на самом листе перечня. Замер на реальном томе:
на листе содержания обозначений нормативов 0, на листе перечня — 25.
Поэтому страница считается перечнем только при `MIN_DESIGNATIONS` реальных
обозначений на ней, а не по одному заголовку.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import LlmConfig, call_llm_json
from .requirement_registry import Requirement

# Заголовок раздела с перечнем. Формулировка у разных проектировщиков плывёт,
# поэтому ищется корнями, а не дословной фразой (тот же приём, что Г.59).
_HEADING_RE = re.compile(
    r"перечень\s+использованн\w*\s+нормативн|перечень\s+нормативн\w*|"
    r"нормативные\s+ссылки|перечень\s+использованн\w*\s+литератур",
    re.IGNORECASE)

# Обозначение нормативного документа. Список префиксов — по действующим
# системам обозначений; к разделу проектной документации не привязан.
_DESIGNATION_RE = re.compile(
    r"\b(СП|СНиП|ГОСТ\s+Р|ГОСТ|СанПиН|СанПин|СН|НПБ|ВСН|МДС|ПУЭ|ТР\s*ТС|ФЗ)"
    r"\s*([0-9Р][0-9.\-–*/]*)",
    re.IGNORECASE)

# Сколько обозначений должно стоять на странице, чтобы считать её перечнем, а
# не строкой содержания. Замер на реальном томе: содержание 0, перечень 25.
MIN_DESIGNATIONS = 3


@dataclass
class Norm:
    """Один норматив из перечня тома.

    `aliases` — обозначения того же документа в прежней системе, которые в
    перечне записаны в скобках сразу после основного. Требование в тексте
    может ссылаться на любое из них, и без псевдонимов ссылка на старое
    обозначение выглядела бы как норматив вне перечня.
    """
    designation: str        # обозначение документа целиком, как в перечне
    title: str = ""         # наименование из кавычек, если оно есть
    page: int = 0
    document: str = ""
    aliases: list[str] = field(default_factory=list)

    def label(self) -> str:
        base = f"{self.designation} «{self.title}»" if self.title else self.designation
        return f"{base} ({', '.join(self.aliases)})" if self.aliases else base


NORM_SOURCE_CITED = "названа в требовании"
# Обозначение стоит в тексте требования, но в перечне тома его нет. Это НЕ
# ошибка и не нарушение — прямое уточнение пользователя: «если в тексте есть
# ссылка на норму, которой нет в списке, это не ошибка, просто надо это
# учитывать». Ссылка при этом полноценная: она названа проектировщиком, и
# терять её из-за неполноты перечня значило бы прятать от инспектора то, что
# в документе написано прямым текстом.
NORM_SOURCE_CITED_UNLISTED = "названа в требовании (в перечне тома нет)"
NORM_SOURCE_MATCHED = "сопоставлено по смыслу"


def _clean_designation(raw: str) -> str:
    """Обозначение без хвостовой пунктуации предложения.

    Найдено замером: регулярка захватывала точку в конце фразы, и
    обозначение переставало сходиться со своей же строкой в перечне —
    норматив, который в перечне ЕСТЬ, попадал в список отсутствующих. Тот
    же класс ошибки, что Г.68: захваченный разделитель даёт не честный
    промах, а неверные данные.
    """
    return " ".join(raw.split()).rstrip(".,;:–- ")


def _normalize(designation: str) -> str:
    """Ключ сравнения обозначений: пробелы, звёздочки редакции и хвостовая
    пунктуация не значимы.

    Обозначение со звёздочкой и без неё — один документ: звёздочка означает
    редакцию с изменениями и в тексте требования обычно опускается.
    """
    return re.sub(r"[\s*]+", "", _clean_designation(designation)).upper()


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Границы «…» — внутри наименования норматива обозначения не считаются
    отдельными документами. Наименование действующего документа нередко
    целиком содержит обозначение заменённого, и без этой границы из одной
    строки перечня получалось бы два документа."""
    return [(m.start(), m.end()) for m in re.finditer(r"«[^»]{0,400}»", text)]


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def parse_norms(text: str, page: int = 0, document: str = "") -> list[Norm]:
    """Нормативы со страницы перечня: обозначение, наименование, псевдонимы.

    Два случая, из-за которых наивный разбор ломается, найдены замером на
    реальном перечне, а не предположены:

    1. Обозначение в СКОБКАХ сразу после основного — тот же документ в
       прежней системе обозначений. Наивный разбор заводил из него отдельный
       норматив И отдавал ему наименование, а основной оставался безымянным.
    2. Обозначение внутри КАВЫЧЕК входит в наименование и отдельным
       документом не является.
    """
    quoted = _quoted_spans(text)
    matches = [m for m in _DESIGNATION_RE.finditer(text) if not _inside(m.start(), quoted)]

    primaries: list[tuple] = []  # (match, [псевдонимы])
    for m in matches:
        before = text[:m.start()].rstrip()
        is_alias = bool(primaries) and before.endswith("(")
        if is_alias:
            primaries[-1][1].append(_clean_designation(m.group(0)))
        else:
            primaries.append((m, []))

    out: list[Norm] = []
    seen: set[str] = set()
    for i, (m, aliases) in enumerate(primaries):
        designation = _clean_designation(m.group(0))
        key = _normalize(designation)
        if key in seen:
            continue
        end = primaries[i + 1][0].start() if i + 1 < len(primaries) else len(text)
        title_match = re.search(r"«([^»]{4,300})»", text[m.end():end])
        title = " ".join(title_match.group(1).split()) if title_match else ""
        seen.add(key)
        out.append(Norm(designation=designation, title=title, page=page,
                        document=document, aliases=aliases))
    return out


def norms_from_llm(items: list[dict], document: str = "") -> list[Norm]:
    """Перечень, прочитанный моделью попутно с выжимкой (Г.103).

    Основной путь. Заголовок раздела с перечнем у каждого проектировщика
    свой («перечень использованных нормативных документов», «нормативные
    ссылки», «перечень использованной литературы» и десяток других
    формулировок), и зашивать их список в регулярку — заточка под те
    документы, которые уже видели. Документ и так уходит в модель ради
    выжимки, поэтому перечень забирается тем же вызовом.

    Ответ модели — недоверенные данные: обозначение принимается, только
    если оно похоже на обозначение нормативного документа. Так модель не
    может дописать в перечень то, чего на странице нет (раздел 0, п.7).
    """
    out: list[Norm] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = _clean_designation(str(item.get("designation") or ""))
        if not raw or not _DESIGNATION_RE.fullmatch(raw):
            continue
        key = _normalize(raw)
        if key in seen:
            continue
        seen.add(key)
        page = item.get("page")
        out.append(Norm(designation=raw,
                        title=" ".join(str(item.get("title") or "").split())[:300],
                        page=page if isinstance(page, int) else 0,
                        document=document))
    return out


def find_norms(text_facts: list[dict]) -> list[Norm]:
    """Перечень нормативов тома по тексту — ЗАПАСНОЙ путь без ключа (Г.103).

    Ищет раздел по заголовку из закрытого списка формулировок. Список
    заведомо неполный: назвать раздел можно по-разному, и предугадать все
    варианты нельзя — поэтому основной путь `norms_from_llm`, а этот
    работает, когда ключа нет, и заодно служит перекрёстной проверкой
    (Г.92: детерминированный путь ценен не только как запасной).

    Пустой список означает «раздела перечня не найдено» — видимое
    состояние, а не повод угадывать нормативы по разделу (Г.10)."""
    out: list[Norm] = []
    seen: set[str] = set()
    for fact in text_facts:
        text = fact.get("text") or ""
        if not _HEADING_RE.search(text):
            continue
        if len(_DESIGNATION_RE.findall(text)) < MIN_DESIGNATIONS:
            continue  # лист содержания: заголовок есть, самих нормативов нет
        for norm in parse_norms(text, page=int(fact.get("page") or 0),
                                document=str(fact.get("document") or "")):
            if _normalize(norm.designation) not in seen:
                seen.add(_normalize(norm.designation))
                out.append(norm)
    return out


def link_cited(requirements: list[Requirement], norms: list[Norm]) -> list[Requirement]:
    """Привязка по прямому упоминанию: вызова модели не требует и ошибиться
    не может. Возвращает требования, оставшиеся без норматива."""
    by_key: dict[str, Norm] = {}
    for norm in norms:
        for name in [norm.designation, *norm.aliases]:
            by_key.setdefault(_normalize(name), norm)
    pending: list[Requirement] = []
    for req in requirements:
        haystack = f"{req.sentence} {req.summary} {req.code or ''}"
        found: Norm | None = None
        unlisted: str = ""
        for m in _DESIGNATION_RE.finditer(haystack):
            designation = _clean_designation(m.group(0))
            norm = by_key.get(_normalize(designation))
            if norm is not None:
                found = norm
                break
            if not unlisted:
                unlisted = designation
        if found is not None:
            req.norm = found.designation
            req.norm_source = NORM_SOURCE_CITED
            continue
        if unlisted:
            # Норматив назван проектировщиком, но в перечень тома не внесён.
            # Ссылку сохраняем: перечень мог быть оформлен в другом томе
            # комплекта, а прятать от инспектора то, что написано в
            # документе прямым текстом, — потеря данных, а не строгость.
            req.norm = unlisted
            req.norm_source = NORM_SOURCE_CITED_UNLISTED
            continue
        pending.append(req)
    return pending


def cited_outside_the_list(requirements: list[Requirement],
                          norms: list[Norm]) -> dict[str, list[int]]:
    """Нормативы, названные в тексте, но ОТСУТСТВУЮЩИЕ в перечне тома.

    Найдено замером, а не предположено: в одном предложении названы два
    норматива — на материал покрытия и на грунт под него, — а в перечень
    использованных нормативных документов попал только один из них.
    Проектировщик сослался на документ, которого сам не объявил.

    Это не нарушение и здесь так не называется: перечень мог быть оформлен в
    другом томе комплекта, а ссылка — оказаться справочной. Но проверить
    полноту перечня инспектор может только если ему об этом сказали, а
    молчание тут неотличимо от «всё сошлось» (Г.10). Возвращает
    {обозначение: [страницы]}.
    """
    known: set[str] = set()
    for norm in norms:
        known.update(_normalize(n) for n in [norm.designation, *norm.aliases])

    out: dict[str, list[int]] = {}
    for req in requirements:
        for m in _DESIGNATION_RE.finditer(f"{req.sentence} {req.summary}"):
            designation = _clean_designation(m.group(0))
            if _normalize(designation) in known:
                continue
            pages = out.setdefault(designation, [])
            if req.page not in pages:
                pages.append(req.page)
    return {k: sorted(v) for k, v in sorted(out.items())}


LINK_SYSTEM_PROMPT = """Ты помогаешь инспектору государственного строительного надзора
понять, к какому нормативному документу относится параметр или решение,
выписанное из проектной документации.

ВЫБИРАЙ ТОЛЬКО ИЗ СПИСКА НИЖЕ. Список — это перечень нормативных документов,
объявленный в самом проверяемом томе. Норматива, которого в списке нет,
для тебя не существует, даже если ты уверен, что он подходит: проект
выполнен по объявленному перечню, и ссылаться инспектор будет на него.

Не называй пункты, разделы и таблицы внутри норматива — только сам документ.
Пункт, названный по памяти, выглядит как точная ссылка и потому опаснее
отсутствия ссылки.

Если требование не относится ни к одному документу списка или ты не уверен —
верни norm_index = null. Пустой ответ здесь нормален и част: организационные
решения, марки конкретных изделий и описания состава работ часто не
привязаны ни к одному нормативу из перечня. Угаданная привязка хуже
отсутствующей.

Отвечай только JSON без пояснений вне JSON:
{"links": [{"index": <номер требования из списка>, "norm_index": <номер норматива> или null}]}"""


def _render_norms(norms: list[Norm]) -> str:
    return "\n".join(f"{i}. {n.label()}" for i, n in enumerate(norms))


def link_by_meaning(requirements: list[Requirement], norms: list[Norm],
                    config: LlmConfig | None,
                    batch_size: int = 25,
                    timeout: float = 120.0,
                    on_error=None) -> int:
    """Сопоставление оставшихся требований с перечнем по смыслу.

    Пачками, а не по одному требованию: на томе это сотни требований, и
    вызов на каждое сделал бы шаг дороже самого извлечения (тот же довод,
    что в Г.49). Ответ — по ИНДЕКСУ, а не по тексту: при похожих
    формулировках сопоставление по тексту промахивается.

    Возвращает число привязанных требований. Сбой пачки не роняет остальные
    и не превращается в «норматива нет» — требование просто остаётся без
    привязки, что честно означает «не сопоставляли» (Г.10/Г.77).
    """
    if config is None or not norms or not requirements:
        return 0
    linked = 0
    norm_block = _render_norms(norms)
    for start in range(0, len(requirements), batch_size):
        batch = requirements[start:start + batch_size]
        listing = "\n".join(
            f"{i}. {r.summary or r.sentence}" for i, r in enumerate(batch))
        user = (f"НОРМАТИВНЫЕ ДОКУМЕНТЫ ТОМА:\n{norm_block}\n\n"
                f"ТРЕБОВАНИЯ:\n{listing}")
        try:
            result = call_llm_json(config, LINK_SYSTEM_PROMPT, user, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — сбой пачки не роняет остальные
            if on_error:
                on_error(start, exc)
            continue
        if not result:
            continue
        for item in result.get("links", []):
            idx, norm_idx = item.get("index"), item.get("norm_index")
            if not isinstance(idx, int) or not isinstance(norm_idx, int):
                continue
            if not (0 <= idx < len(batch)) or not (0 <= norm_idx < len(norms)):
                continue  # модель назвала норматив вне перечня — отбрасываем
            batch[idx].norm = norms[norm_idx].designation
            batch[idx].norm_source = NORM_SOURCE_MATCHED
            linked += 1
    return linked


def render_norms_section(norms: list[Norm], requirements: list[Requirement]) -> str:
    """Раздел отчёта: какие нормативы объявлены в томе и сколько требований к
    каждому привязано.

    Отсутствие перечня — отдельная строка с причиной, а не пустой раздел:
    «нормативы не показаны» и «в томе нет перечня» требуют от инспектора
    разного (Г.10).
    """
    if not norms:
        return ("\n=== Нормативная база ===\n"
                "  Перечень нормативных документов в томе не найден — привязка к нормам\n"
                "  не выполнялась. Это не значит, что проект выполнен без нормативов:\n"
                "  перечень может быть в другом томе комплекта или оформлен иначе.")

    counts: dict[str, int] = {}
    cited: dict[str, int] = {}
    for req in requirements:
        if not req.norm:
            continue
        counts[req.norm] = counts.get(req.norm, 0) + 1
        if req.norm_source in (NORM_SOURCE_CITED, NORM_SOURCE_CITED_UNLISTED):
            cited[req.norm] = cited.get(req.norm, 0) + 1

    lines = [f"\n=== Нормативная база тома ({len(norms)} документов) ==="]
    unlinked = sum(1 for r in requirements if not r.norm)
    for norm in norms:
        total = counts.get(norm.designation, 0)
        if total:
            direct = cited.get(norm.designation, 0)
            mark = f" — требований: {total}"
            if direct:
                mark += f", из них названа в тексте: {direct}"
        else:
            mark = ""
        lines.append(f"  {norm.label()}{mark}")
    lines.append(f"  Без привязки к нормативу: {unlinked} из {len(requirements)} — "
                 f"это нормально: марки изделий и состав работ нормативу перечня "
                 f"часто не соответствуют напрямую.")

    outside = cited_outside_the_list(requirements, norms)
    if outside:
        lines.append("\n  Названы в тексте требований, но в перечне тома не значатся. "
                     "Это НЕ нарушение:\n  перечень мог быть оформлен в другом томе "
                     "комплекта, а ссылка — быть справочной.\n  Учитывать при проверке:")
        for designation, pages in outside.items():
            pages_str = ", ".join(str(p) for p in pages[:8])
            more = " и др." if len(pages) > 8 else ""
            lines.append(f"    {designation} — стр. {pages_str}{more}")
    return "\n".join(lines)
