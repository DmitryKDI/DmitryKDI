# GIGACODE.md — роль локального диагноста

Рабочая ветка: `claude/new-session-d44es2`.
Перед работой читай `.claude/PROJECT-NADZOR.md`, `CONTEXT.md`, `CURRENT-TASK.md`.

## Роль

Главная ценность локальной сессии — реальные GigaChat credentials, реальные
документы и фактические прогоны. Не угадывай результат модели.

Если пользователь прямо не просил редактировать код, диагностируй и сохраняй
логи локально. Если пользователь дал право на изменения — всё равно не трогай
`main` и не коммить реальные данные объекта.

## Активный runtime

```text
document map + PD requirements
-> stateful investigator conversation
-> Python search/open/zoom tools
-> self-review
-> independent verifier
```

Ключевые файлы:
- `packages/backend/app/stateful_investigator.py`
- `packages/backend/app/conversation_llm.py`
- `packages/backend/app/inspector_memory.py`
- `packages/backend/app/lean_analysis_runtime.py`

Старые pair/requirement micro-runtimes не являются active path.

## Три правила

1. Тишина != чисто.
2. Не знаешь — смотри реальный log/output.
3. Реальные документы, имена объекта и сырые findings не пушить в git.

## GigaChat peer review

После fresh run:

```bash
python scripts/gigachat_current_review.py
```

Он должен ревьюить именно stateful architecture и actual investigator metrics.

## Learning from mistakes

Experienced/demo mode может использовать local generalized lessons:

```bash
python scripts/teach_investigator.py --feedback "..." --source-type teacher
```

При `NADZOR_BLIND_BENCHMARK=1` lessons не загружаются вообще.

## Что проверять в новом run

- requests/responses/errors/cache hits;
- investigator turns/actions;
- `finished`;
- `self_reviewed`;
- `turn_budget_exhausted`;
- pages inspected;
- zoom regions;
- candidates;
- verifier confirmed/unresolved/errors;
- external evaluator отдельно.

Никаких выводов «модель ничего не нашла» при technical incomplete.
