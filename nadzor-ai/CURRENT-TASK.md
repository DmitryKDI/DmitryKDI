# CURRENT-TASK.md

Текущая задача: устойчивый полный blind-прогон ПД -> РД/ИД через stateful GigaChat investigator.

## Active runtime

`document map -> stateful investigator -> search/read_text/inspect_pages/zoom -> inline verifier -> self-review`

Ключевые правила полного прогона:
- turn budget масштабируется от числа страниц и требований, но ограничен cap;
- длинный текст страниц не перегружает стартовый prompt: полный индекс доступен через `search`;
- `read_text` используется отдельно от visual inspection;
- verifier вызывается сразу после `propose_finding`; `needs_more` возвращает investigator к поиску evidence;
- multi-room requirements контролируются per-target, а не одним общим compliance-флагом;
- render/provider/tool failure означает неопределённость, а не отсутствие;
- перед finish обязателен self-review;
- `NADZOR_BLIND_BENCHMARK=1` отключает learned memory;
- benchmark ground truth не попадает в runtime/prompts/routing.

После каждого изменения:
1. работать только в `claude/new-session-d44es2`;
2. проверить CI именно на текущем SHA;
3. затем делать fresh run через `scripts/autoloop.py`;
4. оценивать `finished`, `self_reviewed`, turns, visual/text coverage, verifier outcomes, tool/provider errors и внешний evaluator;
5. `main` не менять и не мержить.
