# НАДЗОР.ИИ — активный handoff

Рабочий корень: `nadzor-ai/`.
Рабочая ветка: `claude/new-session-d44es2`.
`main` автоматически не менять и не мержить.

## Главная цель

НАДЗОР.ИИ сравнивает ПД -> РД/ИД и выдаёт инспектору конкретные точки контроля
с доказательствами. Система не должна подгоняться под известный benchmark:
runtime не получает expected room/page/equipment ids и known violations.

## Активная архитектура

С 2026-09-13 активный runtime — **stateful GigaChat investigator**.

```text
PD/RD documents
  -> compact map всех страниц + извлечённые требования ПД
  -> одна stateful conversation GigaChat-3-Ultra
  -> Python tools: search / open page / zoom
  -> обязательный self-review против false negatives
  -> candidate findings
  -> отдельный independent verifier
  -> confirmed findings + unresolved candidates
```

Почему: обычный чат GigaChat держит историю, а прежняя API-механика разбивала
одну инженерную задачу на сотни изолированных микрозапросов, где модель каждый
раз начинала с нуля. Теперь история расследования передаётся между ходами.

### Роли

**GigaChat investigator**
- сам решает, что смотреть дальше;
- помнит предыдущие страницы/выводы;
- не обязан на каждом ходу заполнять огромный semantic contract;
- не имеет права окончательно подтвердить finding.

**Python**
- открывает страницу;
- отдаёт text layer;
- делает search только для навигации;
- рендерит zoom;
- не решает семантику изменения.

**Independent verifier**
- получает candidate + реальные evidence;
- требует PD observation + RD observation + direct contradiction;
- `NOT_OBSERVED != ABSENCE`;
- presence/absence подтверждается только при достаточном scope.

## Опыт и обучение

Есть локальная память `data/inspector_memory.sqlite3`:
- teacher/evaluator feedback обобщается в generic lesson;
- lesson используется только как стратегия поиска, не как evidence;
- конкретные имена файлов, листы, помещения и ground truth не должны становиться
  runtime-правилами;
- при `NADZOR_BLIND_BENCHMARK=1` learned memory полностью отключена.

Поэтому:
- **blind mode** — честная проверка обобщающей способности;
- **experienced/demo mode** — можно использовать накопленный опыт.

Команда для обучения из обратной связи:

```bash
python scripts/teach_investigator.py --feedback-file feedback.txt --source-type teacher
```

## Правила качества

1. Тишина не означает «всё чисто».
2. Ошибка provider/verifier не превращается в no-finding.
3. Неполный turn budget не превращается в safe no-change.
4. Multi-room requirement нельзя считать проверенным по одной зоне.
5. Different sheet genre сам по себе не finding.
6. Search/routing/anchors — navigation only.
7. Каждый confirmed finding проходит независимый verifier.
8. Перед `finish` обязателен self-review.
9. Для выставки приоритет recall/depth выше экономии токенов.
10. Все циклы bounded: max turns, max iterations, timeouts.

## Benchmark

`data/known_violations.json` остаётся пустым в blind validation.
Ground truth живёт только во внешнем evaluator.
Нельзя добавлять expected rooms/pages/findings в prompts, runtime, tests или
learned memory blind-режима.

## Автоматический цикл разработки

```text
git sync
-> clean workspace + fresh semantic cache
-> backend
-> blind run
-> logs
-> GigaChat peer review
-> external evaluator
-> generic code/architecture fix
-> tests/CI
-> next fresh iteration
```

Никогда не auto-merge `main`.

## Что читать агентам

В начале сессии:
1. этот файл;
2. `nadzor-ai/CONTEXT.md`;
3. `nadzor-ai/CURRENT-TASK.md`;
4. активный код:
   - `app/stateful_investigator.py`
   - `app/conversation_llm.py`
   - `app/inspector_memory.py`
   - `app/lean_analysis_runtime.py`
5. свежий `data/run_logs/.../triangulated/latest.json`, если он есть.

Legacy modules (`semantic_pair_runtime.py`, `semantic_requirement_runtime.py`,
room/equipment/routing/verdict layers) сохраняются для regression/forensics, но
не должны возвращаться в active path без реального evidence, что это нужно.

## Проверка перед утверждением успеха

Не говорить «готово/green/3 из 3», пока не проверены:
- текущий Git SHA;
- CI именно для этого SHA;
- fresh run metrics;
- investigator `finished=true`;
- `self_reviewed=true`;
- provider/verifier errors;
- external evaluator result.
