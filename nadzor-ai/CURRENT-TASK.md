# CURRENT-TASK.md

Текущая задача: довести stateful GigaChat investigator до устойчивого blind
поиска расхождений ПД -> РД/ИД без benchmark hardcoding.

## Сейчас делать

1. Работать только в `claude/new-session-d44es2`.
2. Проверять active runtime:
   `stateful_investigator -> tools -> self-review -> verifier`.
3. После кода запускать targeted tests/CI.
4. Потом делать новый fresh blind run через autoloop.
5. После run запускать `scripts/gigachat_current_review.py`.
6. Сравнивать результат с предыдущими handoff/trend.
7. Только внешний evaluator знает ground truth.

## Не делать

- Не возвращать старые room/equipment/routing hard gates в active path.
- Не добавлять номера benchmark помещений/листов/оборудования в код/промпты.
- Не считать cached run доказательством.
- Не считать `finish` надёжным, если self-review не выполнен.
- Не считать candidate confirmed без verifier.
- Не менять `main`.

## Experienced/demo learning

Чтобы показать investigator ошибку и сохранить общий урок:

```bash
python scripts/teach_investigator.py --feedback-file feedback.txt --source-type teacher
```

Lesson хранится локально. Blind benchmark его не увидит.

## Что присылать пользователю после нового прогона

Коротко:
- SHA;
- fresh/cache status;
- requests/errors;
- turns/actions;
- pages/zooms;
- self-review;
- candidates / confirmed / unresolved;
- evaluator score, если есть;
- что улучшилось/ухудшилось относительно истории.
