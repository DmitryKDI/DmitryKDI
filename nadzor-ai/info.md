# Info

Краткий контекст текущего состояния проекта НАДЗОР.ИИ.

## Что уже сделано

- Backend и frontend запускались локально на `127.0.0.1:8010` и `localhost:5173`.
- Модель провайдера переключена на `GigaChat-3-Ultra`.
- Исправлена проблема `sqlite3.OperationalError: database is locked` в `packages/backend/app/file_store.py`.
- Для `file_store` добавлены `busy_timeout` и `WAL`, а также тестовая проверка этих настроек.
- `START-NADZOR.bat` обновлён и снова запускает и backend, и frontend через `scripts/start-all.sh`.

## Важные файлы

- `START-NADZOR.bat`
- `packages/backend/app/file_store.py`
- `packages/backend/tests/test_file_store.py`
- `packages/backend/tests/test_constants_are_declared.py`

## Текущее состояние

- Локальный backend отвечал `health = ok`.
- Список документов на сервере был виден через API.
- В рабочем дереве есть незакоммиченные изменения, их нельзя терять.

## Что помнить после перелогина

- Не откатывать изменения в файлах выше.
- Если снова появится зависание загрузки, сначала проверить, жив ли backend и нет ли повторного `database is locked`.
- Батник теперь должен запускать обе части системы сразу.
