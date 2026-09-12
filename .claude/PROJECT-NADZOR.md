# НАДЗОР.ИИ — активный handoff для Claude Code

Дата состояния: 2026-09-12. Репозиторий: `DmitryKDI/DmitryKDI`. Рабочая ветка: `claude/new-session-d44es2`. Активный проект находится в `nadzor-ai/`.

## Старт каждой сессии

Сразу считай `nadzor-ai/` рабочим корнем проекта. Не трать время на обзор всего профиля-репозитория. Первые действия:

```bash
cd nadzor-ai
rtk git status
rtk git fetch origin
rtk git log -8 --oneline
```

Если локальная ветка уже `claude/new-session-d44es2`, подтяни её fast-forward без merge-коммитов. Никогда не мержи `main` и не пушь изменения в `main`. Все изменения делай только в `claude/new-session-d44es2`, если пользователь прямо не дал другую ветку.

Перед крупным изменением прочитай этот файл, текущие реализации в `packages/backend/app/`, актуальные тесты и свежие `data/run_logs`. Не переписывай рабочую архитектуру ради «красоты»: сначала найди конкретную причину false negative, технического invalid-run или деградации coverage.

## Главная цель

НАДЗОР.ИИ — blind-пайплайн сравнения строительной проектной документации: ПД -> РД/ИД. Главная задача сейчас — повысить recall инженерных изменений на плотных чертежах и при этом не подгонять runtime под известный benchmark.

Критическое правило: runtime не должен знать эталонные нарушения, ожидаемые номера помещений, ожидаемые листы или benchmark-derived hints. `data/known_violations.json` должен оставаться пустым. Значение «3/3» может появляться только как внешняя оценка после завершённого blind-прогона и никогда не должно быть целью конкретного if/regex/prompt branch.

Не добавляй hardcoded room/page/equipment identifiers из тестового комплекта в runtime, prompts, routing, fixtures или эвристики. Все изменения должны быть универсальными для произвольных комплектов ПД/РД/ИД.

## Что было сделано сегодня

### 1. Разбор реального прогона

По последнему переданному `run_logs.zip` было установлено:

- PD run 12 завершился: 15 requests, 14 responses, 1 error, 6 image uploads, около 97.9 s, 8 requirements, `failed_chunks=0`.
- Compliance run 12 завершился: 19 requests, 18 responses, 1 error, 6 image uploads, 4 image cache hits, около 122.7 s; 8 requirements, из них 7 «требует проверки» и 1 «исходные данные».
- Analysis run 12 завершился на `GigaChat-3-Ultra`: 4 page pairs, 0 LLM errors, 3 drawing pairs matched by text, score примерно 0.6675–0.7022, 2 text findings, около 33.3 s.
- `tasks/triangulated/latest.json` относился к run 11, а не 12. Он имел `details.valid=false`, причина: сторона ПД оказалась пустой `(ПД 0/1, РД 2/2)`. В `load_documents` был `NoneType object is not callable`; pipeline остановился примерно на 0.624 s, было 0 requests и 0 image uploads.

Вывод: тот архив логов НЕ проверял новую high-recall vision-механику. ROOM/generic anchors, raster navigation, PASS1, PASS2 и triangulation vision вообще не дошли до выполнения. Нельзя интерпретировать этот run как «vision ничего не нашёл».

### 2. Найдена и исправлена гонка lazy SQLAlchemy session factory

В `facts_store.py` была реальная race-схема: `_engine` мог уже быть опубликован, а `_Session` ещё оставаться `None`, после чего другой thread вызывал `_Session()` и получал `NoneType object is not callable`.

Исправлено через `threading.RLock()`: engine и session factory строятся во временных переменных и публикуются атомарно; повторная инициализация происходит, если отсутствует либо `_engine`, либо `_Session`, либо сменился URL. Добавлены concurrency regression tests, включая многопоточный запуск.

### 3. ROOM high-recall vision

Текущая ROOM-механика:

- один ROOM на один PASS1;
- модель получает 4 отдельных изображения, не paired montage: `PD general`, `PD room`, `RD general`, `RD room`;
- ROOM — navigation anchor, не доказательство изменения;
- локальный ROI строится не как маленькая рамка вокруг цифры помещения, а расширяется по векторной геометрии и контексту границ/подключений;
- PASS1 отдельно перечисляет engineering inventory, topology, connections, parameters, затем формирует `candidate_differences`;
- PASS2 проверяет только кандидатов;
- `unclear` не превращается в `same`;
- low/medium comparability не имеет права удалить candidate через `rejected`.

Добавлен explicit `comparability=high|medium|low` в ROOM PASS1/PASS2. `rejected` разрешён только при high comparability и ясном визуальном опровержении. При medium/low verdict принудительно сохраняется как `unclear`. Это fail-closed поведение против false negative.

### 4. Чертежи без экспликации/ROOM

Реализована иерархия универсальных anchors:

1. ROOM, если он есть;
2. axes/grid;
3. equipment/system tags;
4. vector geometry clusters;
5. model-proposed PASS0 regions;
6. whole-page semantic fallback.

Файл `comparison_anchors.py` извлекает axis и equipment/system anchors из текста листа. `control_pair_candidates.py` может расширять candidate page pairs не только по room overlap, но и по non-room anchor overlap. Один слабый axis token не считается достаточным; для осей используется multi-axis evidence.

Файл `generic_region_vision.py` делает локальное сравнение для чертежей без надёжных ROOM labels. Он строит deterministic region proposals из equipment/system labels, >=2 axes и vector geometry clusters. Geometry fingerprints используются только для навигации/предфильтра, не как semantic proof.

Если deterministic proposals недостаточно, PASS0 может предложить дополнительные comparison regions по двум whole-page изображениям. PASS0 не имеет права создавать findings — только локальные регионы. Для каждой локальной region дальнейший PASS1 снова получает 4 images: PD general/local + RD general/local. PASS2 подтверждает/отклоняет candidates с тем же comparability gate.

Текущая логика discovery-first: бюджет сначала распределяется на несколько PASS1 regions, и только потом оставшиеся вызовы идут на verification. Это уменьшает starvation, когда первый регион съедает весь budget до того, как другие regions были просмотрены.

### 5. Generic vision budget bug

Последняя функциональная правка перед этим handoff: commit `da6bd00365b02e978aac800e1272862e104bbe9a` (`fix: count generic vision failures once against budget`).

До неё exception path в PASS0/PASS1/PASS2 мог дважды увеличивать `used`: один раз до/после provider call и ещё раз в `except`. Из-за этого один неуспешный provider attempt мог съесть два slots и преждевременно лишить следующие regions шанса на анализ. Теперь каждый реальный provider attempt расходует ровно один budget slot независимо от success/failure; счётчик увеличивается до вызова.

### 6. Pair score и raster semantics

`pair.score` — routing/matching similarity, а не «процент расхождения» и не confidence факта изменения. Не выводить его пользователю как severity/change percent. Raster diff тоже только navigation/prioritization evidence. Ни raster similarity, ни anchor score не могут блокировать semantic Vision и не могут сами создать finding.

### 7. Run logging и fail-closed observability

`run_logger.py` изменён так, чтобы task diagnostics сохраняли immutable per-run snapshots и отдельно `latest.json`, а не уничтожали всю историю одной стадии. Это нужно, чтобы не смешивать «latest» разных независимых run-id и не принимать partial execution за completed-no-findings.

Использовать/развивать technical states вида:

- `not_started`
- `started`
- `completed`
- `failed`
- `skipped_due_to_dependency`

И отдельно различать как минимум:

- `technical_invalid`
- `completed_no_findings`
- `completed_with_recovered_errors`

Наличие `status=done` само по себе не означает, что каждый downstream stage реально выполнился. Для анализа всегда смотреть `execution_state`, `technical_status`, `valid`, errors, request/image counts и stage diagnostics.

### 8. Provider scheduling и метрики

GigaChat для текущего пользовательского credential обычно работает с `NADZOR_GIGACHAT_CONCURRENCY=1`. Поэтому несколько background tasks могут выглядеть как `0/N` до окончания первого provider call — это очередь, а не обязательно зависание.

В `llm_runtime.py` добавлялись/расширялись runtime metrics для provider queue/wait observability. При дальнейшей диагностике различай:

- task создан;
- task ждёт provider slot;
- HTTP request отправлен;
- response получен;
- parse/validation failed;
- retry/rate-limit;
- task completed/failed.

Не увеличивай concurrency просто ради визуального ускорения, пока квота конкретного client_id не подтверждена.

## GigaChat peer-review: работать по тому же принципу

Используй `scripts/gigachat_peer_review.py`. Это основной способ «спросить GigaChat о собственной архитектуре» без ручного копирования секретов и без benchmark contamination.

Команда из `nadzor-ai/`:

```bash
rtk python scripts/gigachat_peer_review.py
```

Скрипт:

- сам читает `.env`;
- использует `GIGACHAT_CREDENTIALS` либо `GIGACHAT_CLIENT_ID` + `GIGACHAT_CLIENT_SECRET`, либо существующий backend credential loader;
- не печатает секреты;
- предпочитает реальные runtime logs из `nadzor-ai/data/run_logs` и только как fallback проверяет `nadzor-ai/run_logs`;
- делает два независимых review-call: runtime review и universal vision architecture review;
- автоматически вставляет machine-parsed runtime summary в первый запрос;
- специально не передаёт benchmark ground truth, expected room/page ids и known violations;
- использует retries и `use_cache=False`;
- сохраняет ответ в `nadzor-ai/run_logs/gigachat_peer_review_latest.json`.

После каждого значимого blind-прогона алгоритм работы такой:

```text
1. Проверить свежие data/run_logs и доказать, какие stages реально выполнялись.
2. Запустить scripts/gigachat_peer_review.py.
3. Прочитать run_logs/gigachat_peer_review_latest.json.
4. Отделить generic engineering recommendation от конкретного артефакта одного run.
5. Сверить recommendation с реальным кодом и тестами.
6. Внести только универсальные изменения.
7. Запустить targeted tests + CI.
8. Только после этого делать новый blind-прогон.
```

Не спрашивай у пользователя API secret и не вставляй credentials в код, prompt, log или commit.

## Что GigaChat уже рекомендовал и что считаем действующим направлением

Первый review `GigaChat-3-Ultra` дал полезные generic рекомендации:

- один ROOM на call;
- 4 отдельных изображения лучше paired montage для плотной графики;
- ROOM crop должен сохранять 20–30% контекста, трассы и границы;
- PASS1 должен быть high-recall и независим от PASS2;
- `same` допустим только при хорошей читаемости/полном покрытии;
- ambiguity -> `unclear`;
- raster только navigation/priority;
- сравнивать инженерные entities/connections/parameters, а не архитектуру/штампы;
- schematic vs plan сравнивать по сущностям и связям, не по геометрическому совпадению.

Следующий review по non-room architecture рекомендовал:

- anchors: ROOM strong; axes medium и минимум 2 согласованных; equipment/system medium и лучше несколько согласованных tags/context; geometry weak и только при нескольких stable features;
- axis region строить через grid relationships/relative positions, а не одиночную подпись;
- geometry descriptor развивать через connected vector components, background/grid filtering и topology features;
- hybrid multi-factor pairing; raster не semantic gate;
- equipment ROI — вокруг label + context; geometry ROI — component bbox + context;
- PASS1 contract должен сохранять `anchor_provenance`, `comparability`, `execution_state`;
- verifier может `rejected` только при high comparability;
- whole-page fallback остаётся последним страховочным слоем;
- partial execution нельзя интерпретировать как no findings;
- нужен immutable run correlation/manifest.

Не воспринимай числа из peer review как нормативы. Они исходные engineering heuristics и могут меняться после реальных logs/tests.

## Актуальные ключевые модули

```text
packages/backend/app/comparison_anchors.py
packages/backend/app/control_pair_candidates.py
packages/backend/app/control_pair_runtime.py
packages/backend/app/generic_region_vision.py
packages/backend/app/focused_pair_vision.py
packages/backend/app/high_recall_prompts.py
packages/backend/app/high_recall_roi.py
packages/backend/app/high_recall_calls.py
packages/backend/app/high_recall_normalize.py
packages/backend/app/high_recall_orchestrator.py
packages/backend/app/facts_store.py
packages/backend/app/llm_runtime.py
packages/backend/app/run_logger.py
packages/backend/app/triangulated_pipeline.py
scripts/gigachat_peer_review.py
```

Перед изменением public behavior найди caller и тесты. `focused_pair_vision.py` и `control_pair_vision.py` в основном фасады; core logic разнесён по перечисленным модулям.

## Текущие env defaults, важные для high-recall

Пример в `.env.example` должен оставаться синхронизирован с кодом. Важные параметры:

```env
NADZOR_GIGACHAT_CONCURRENCY=1
NADZOR_MAX_GRAPHICAL_CANDIDATE_PAIRS=18
NADZOR_MAX_FOCUSED_ROOM_VISION_CALLS=36
NADZOR_MAX_FOCUSED_CALLS_PER_PAIR=6
NADZOR_FOCUSED_ROOMS_PER_CALL=1
NADZOR_MAX_FOCUSED_ROOMS_PER_PAIR=20
NADZOR_FOCUSED_CROP_PADDING=0.28
NADZOR_FOCUSED_GENERAL_MAX_DIM=2500
NADZOR_FOCUSED_ROOM_MAX_DIM=2600
NADZOR_MAX_GENERIC_REGION_VISION_CALLS=18
NADZOR_MAX_GENERIC_CALLS_PER_PAIR=4
NADZOR_MAX_GENERIC_REGIONS_PER_PAIR=3
NADZOR_MAX_PASS0_REGIONS_PER_PAIR=4
NADZOR_GENERIC_GENERAL_MAX_DIM=2400
NADZOR_GENERIC_LOCAL_MAX_DIM=2600
```

Локальный `.env` пользователя может содержать старые overrides. При странном поведении сравни effective env с `.env.example`, но не перезаписывай пользовательские secrets/settings без необходимости.

Direct Uvicorn теперь должен читать `nadzor-ai/.env` через bootstrap в `packages/backend/app/__init__.py` до импортов submodules.

## Запуск backend

Windows PowerShell, пользовательский локальный сценарий:

```powershell
cd C:\OSR\DmitryKDI\nadzor-ai
.\.venv\Scripts\Activate.ps1
python -m uvicorn packages.backend.app.main:app --reload --port 8010
```

В Linux/container сначала определи существующее venv/requirements и используй эквивалентную команду. Не удаляй БД «для чистоты»: документы и настройки переживают restart намеренно.

Persistent stores, которые нельзя бездумно удалять:

```text
packages/backend/nadzor.db
data/file_store.db
packages/backend/uploads
```

## Тестирование и CI

Workflow: `.github/workflows/nadzor-ui.yml`. Он должен компилировать/тестировать новые runtime modules, включая non-room vision, comparison anchors, facts-store concurrency, run logger и GigaChat peer-review script.

После изменений запускай targeted tests, затем смотри GitHub Actions. Не говори «CI green», пока соответствующий HEAD действительно не завершился success. Если промежуточные commits красные, проверь самый свежий HEAD: ранее часть failures была вызвана устаревшими regression expectations после намеренного изменения semantics.

Полезные targeted suites:

```text
packages/backend/tests/test_focused_pair_vision.py
packages/backend/tests/test_generic_region_vision.py
packages/backend/tests/test_facts_store_concurrency.py
packages/backend/tests/test_run_logger.py
packages/backend/tests/test_llm_runtime.py
packages/backend/tests/test_control_pair_vision.py
packages/backend/tests/test_triangulated_pipeline.py
packages/backend/tests/test_blind_runtime.py
```

## Benchmark policy

Есть реальные PD/RD документы и mini benchmark, используемые для внешней оценки. Они нужны, чтобы проверить blind runtime после реализации, но ground truth не должен попадать в код или GigaChat peer-review prompt.

Если пользователь просит новый blind-прогон, сначала убедись, что pipeline технически valid и нужные vision stages реально выполнялись. Только после полного прогона можно отдельно оценивать результат по внешнему benchmark. Никогда не заявляй «3/3 достигнуто» без фактической blind-run evidence.

## Что делать следующим

При следующей сессии не начинай архитектуру заново. Сначала:

```text
A. Fetch HEAD ветки и проверить, что нет чужих concurrent changes.
B. Проверить GitHub Actions для текущего HEAD.
C. Проверить, что peer-review script читает actual `data/run_logs` и выдаёт непустой runtime_summary.
D. На новом blind-run убедиться, что triangulated pipeline дошёл до ROOM/non-room Vision, а не умер на document loading.
E. По diagnostics проверить region proposals, PASS0, PASS1, PASS2, comparability, calls_used/budget и fallback reasons.
F. Только после реальных diagnostics корректировать anchor topology / fair scheduling / prompts.
```

Особо перспективные generic улучшения после подтверждённого прогона: axis interval/orientation signature, repeated-tag disambiguation через соседние labels, topology invariants для geometry descriptors, stronger per-anchor diagnostics, end-to-end execution correlation manifest. Не внедряй всё сразу без evidence: минимально достаточная generic правка + regression test + blind rerun.

## Стиль работы с пользователем

Пользователь ожидает, что агент сам вносит правки в ветку и проверяет их, а не выдаёт пачку ручных инструкций. Если задача ясна — делай coherent batch прямо в репозитории, добавляй tests, проверяй CI и сообщай exact commit SHA/status. Не мержи `main`. Не проси повторно прислать секреты. Не делай подгон под benchmark.
