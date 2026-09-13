# НАДЗОР.ИИ — требования к автоматизации разработки

Эти правила фиксируют пользовательские требования к рабочему циклу программы и обязательны для autoloop/агентов.

- Рабочая ветка: `claude/new-session-d44es2`. `main` автоматически не менять.
- Каждый blind-run начинается с очищенного workspace и свежего LLM inference cache; настройки ИИ/API key сохраняются.
- Ground truth benchmark существует только во внешнем evaluator. Runtime, промпты GigaChat и coding-agent не получают ожидаемые комнаты, листы или нарушения.
- После каждого blind-run автоматически выполняется GigaChat peer review.
- Полные runtime-логи, имена реальных документов и локальные пути остаются локально и не публикуются в git.
- Для передачи результата между ChatGPT/GitHub разрешён только санитизированный `handoff/latest_handoff.json`, содержащий выводы review и агрегированные технические метрики без runtime_summary/документов.
- После публикации нового handoff GitHub Actions создаёт или дополняет одну issue и упоминает владельца репозитория, чтобы пришло уведомление.
- Автоматический цикл допускается: git sync -> clean runtime -> backend -> upload -> blind run -> logs -> GigaChat review -> external evaluator -> coding-agent -> tests -> next iteration.
- Цикл ограничен `max_iterations`, временем и внешним evaluator; бесконечной подгонки под один benchmark быть не должно.
- Любые неизвестные локальные изменения блокируют git sync/publish; autoloop не коммитит посторонние файлы.
- Локальная работа остаётся совместимой с Windows/VS Code и существующим `.venv`.
