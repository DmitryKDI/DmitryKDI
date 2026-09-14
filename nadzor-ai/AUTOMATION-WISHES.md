# НАДЗОР.ИИ — требования к автоматизации разработки

- Рабочая ветка: `claude/new-session-d44es2`. `main` автоматически не менять.
- Каждый blind-run начинается с очищенного workspace и fresh semantic LLM inference.
- Ground truth benchmark существует только во внешнем evaluator.
- Runtime/prompts/lessons blind-режима не получают expected rooms/pages/findings.
- После каждого blind-run автоматически выполняется GigaChat peer review.
- Полные runtime-логи и реальные документы остаются локально; в git публикуется только sanitized handoff.
- Новый active runtime: stateful GigaChat investigator -> Python tools -> self-review -> independent verifier.
- Learned inspector memory хранится только локально в SQLite.
- `NADZOR_BLIND_BENCHMARK=1` полностью отключает learned memory.
- Experienced/demo mode может использовать generalized lessons из teacher feedback.
- Автоцикл bounded: git -> clean -> backend -> run -> review -> evaluator -> generic fix -> tests -> rerun.
- Не auto-commit чужие dirty changes и не auto-merge `main`.
- История прогонов хранится отдельно от runtime и используется для trend/regression review.
