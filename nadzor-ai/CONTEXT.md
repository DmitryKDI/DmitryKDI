# CONTEXT.md — текущее состояние НАДЗОР.ИИ

## Этап

Активная задача снова **ПД -> РД/ИД blind semantic comparison**.
Старое описание «РД нет, только ООС/извлечение требований» больше не является
текущей задачей.

## Главное архитектурное решение

Прежний lean runtime был формально проще Python-кодом, но фактически заставлял
GigaChat решать слишком много маленьких задач без общей памяти: scope,
inventory, region discovery, bbox, finding contract и verification шли
раздельными вызовами.

Новый active path:

```text
requirements + document map
-> stateful GigaChat investigator
-> search/open/zoom tools
-> self-review
-> independent verifier
```

Один investigator сохраняет историю разговора между ходами.

## Почему это важно

В свежем прогоне до перехода на stateful architecture было:
- fresh inference;
- 214 requests / 214 responses;
- 0 provider errors;
- 0 semantic cache hits;
- но GigaChat review всё равно оценивал local coverage как недостаточное,
  no-candidate trust как low и absence risk как high.

Следовательно, главным ограничением была уже не сеть/кэш, а механика
взаимодействия с моделью.

## Обучение на ошибках

Локальная `inspector_memory.sqlite3` хранит только generalized lessons.
Blind benchmark всегда запускается с `NADZOR_BLIND_BENCHMARK=1` и не видит
эту память. Experienced/demo mode может использовать lessons.

Это позволяет говорить модели «ты ошибся, надо было проверить вот так»,
обобщать урок и использовать его дальше, не выдавая повторный benchmark за
blind 3/3.

## Постоянные правила

- Не подгонять runtime под эталон.
- Не коммитить реальные документы/сырые runtime data.
- `main` не трогать.
- Error != no finding.
- Not observed != absent.
- Routing/search != evidence.
- Multi-room requirement требует покрытия всех относящихся к выводу зон.
- Investigator proposal != confirmed finding.
- Перед finish обязателен self-review.
- Финальный finding должен пройти independent verifier.

## Главные файлы

- `packages/backend/app/stateful_investigator.py`
- `packages/backend/app/conversation_llm.py`
- `packages/backend/app/inspector_memory.py`
- `packages/backend/app/lean_analysis_runtime.py`
- `scripts/gigachat_current_review.py`
- `scripts/teach_investigator.py`
- `scripts/autoloop.py`

Legacy runtime сохраняется только для regression/forensics.

## Следующий критерий прогресса

После каждого изменения нужен fresh blind run и внешний evaluator.
Смотреть не только число findings, но и:
- turns used;
- action pattern;
- pages inspected;
- zoom regions;
- self_reviewed;
- turn budget exhausted;
- candidates / verifier confirmed / unresolved;
- provider/verifier errors.
