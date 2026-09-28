# Модель угроз

Документ перечисляет угрозы, меры, реализованные в коде, и тесты, которые их
проверяют. Меры, которые решение не реализует, названы отдельно — выдавать их
за сделанное нельзя.

## Общие положения

Документы предоставляет поднадзорное лицо — сторона, заинтересованная в
сокрытии нарушений. Поэтому входные данные считаются недоверенными по
умолчанию, а вывод системы — гипотезой для инспектора, а не заключением.

## У-1. Инъекция инструкций через проверяемый документ

| Мера | Реализация | Проверка |
|---|---|---|
| Текст документа — данные, а не инструкции | `UNTRUSTED_INPUT_RULE` в каждом промпте (`app/vision.py`, `app/official_pipeline.py`) | `test_official_pipeline.py::test_document_text_is_sent_as_untrusted_data` |
| Только структурированный ответ | ответ модели разбирается как JSON; неразобранный ответ — техническая ошибка параметра, а не «нарушений нет» | `test_official_pipeline.py::test_missing_model_is_reported_for_every_parameter_and_not_as_clean_result` |
| Цитата обязана существовать на указанном листе | код ищет цитату в тексте страницы и строит bbox по найденному месту; непроверенная цитата не становится доказательством | `test_extraction_evidence.py`, `test_official_pipeline.py::test_candidate_needs_verified_quotes_and_boxes_from_both_sides` |
| У модели нет инструментов, базы и сети | модель получает текст и изображения страниц и возвращает JSON | устройство `app/llm.py` |
| Решение принимает человек | `CONFIRMED_VIOLATION` ставит только инспектор; финализация — только когда у всех кандидатов есть решение | `test_official_api.py`, `test_api_v1.py` |

## У-2. Утечка документов за пределы контура

| Мера | Реализация | Проверка |
|---|---|---|
| Модель только локальная | `app/llm.py`: адрес вне контура отклоняется до отправки запроса | `test_llm.py::test_external_address_is_refused_before_sending` |
| Перечень моделей тоже не запрашивается наружу | `llm.available_models` | `test_model_availability.py::test_external_server_address_is_refused` |
| Сеть модели без выхода наружу | сеть `contour` в `docker-compose.yml` объявлена `internal` | проверено запуском состава |
| Распознавание сканов локально | Tesseract в образе backend | `test_local_ocr.py` |

## У-3. Подмена документа или редакции

| Мера | Реализация | Проверка |
|---|---|---|
| Отпечаток SHA-256 каждого файла | хранилище оригиналов по отпечатку (`app/file_store.py`); SHA-256 из реестра сверяется с полученным файлом | `test_api_v1.py` |
| Актуальная редакция по цепочке замены | `select_current_documents`: неоднозначная цепочка даёт `CLARIFICATION_REQUIRED` | `test_official_pipeline.py::test_current_revision_requires_an_unambiguous_replacement_chain` |
| Цепочка предшественников без циклов | проверка при сохранении метаданных | `test_official_api.py::test_revision_predecessor_cannot_form_a_cycle` |
| Документ из протокола не удаляется | удаление отклоняется | `test_official_api.py::test_document_used_by_an_official_protocol_cannot_be_deleted` |
| Воспроизводимость | в протоколе `input_manifest_hash`, версии матрицы, модели и набора данных | `test_protocol.py` |

## У-4. Отказ в обслуживании и вредоносные файлы

| Мера | Реализация | Проверка |
|---|---|---|
| Тип по содержимому, а не по расширению | `app/document_convert.py`, проверка сигнатуры PDF | `test_upload_storage.py::test_not_a_pdf_is_refused_by_signature_not_by_extension` |
| XML с DTD и сущностями отклоняется (XXE) | `document_convert` до разбора XML | `test_document_convert.py::test_xml_with_dtd_is_refused` |
| Лимиты: 50 МБ на файл, 200 МБ на пакет, число страниц | `app/api_v1.py`, `app/main.py` | `test_api_v1.py::test_package_limit_rejects_the_whole_package`, `test_upload_storage.py` |
| Имя файла не участвует в пути | файл хранится под отпечатком | `test_upload_storage.py::test_file_name_never_becomes_the_path` |
| Тяжёлый том режется на части по весу | `app/document_split.py`, нумерация листов сохраняется | `test_upload_storage.py::test_heavy_volume_is_split_into_parts_covering_every_page` |

## У-5. Компрометация цепочки поставки

| Мера | Реализация |
|---|---|
| Версии зависимостей закреплены | `requirements.txt`, `package-lock.json` |
| Образ сервера модели закреплён дайджестом | `docker-compose.yml`, `scripts/offline/prepare_bundle.sh` |
| Офлайн-комплект сверяется с манифестом | `scripts/offline/load_bundle.sh` сравнивает ID образов с `bundle/MANIFEST.txt` |
| Непривилегированный пользователь в образе | `docker/backend.Dockerfile` (uid 10001) |

## У-6. Несанкционированный доступ и отрицание действий

| Мера | Реализация | Проверка |
|---|---|---|
| Всё, кроме входа и `/health`, — только после входа | `app/auth.py`: сессия по cookie (HttpOnly, SameSite=strict) или bearer-токену | `test_auth.py::test_everything_except_login_and_health_requires_a_session` |
| Пароли не хранятся | scrypt с солью, минимальная длина | `test_auth.py::test_password_is_stored_as_a_salted_hash` |
| Роли разграничены | `auth.require`: инспектор, супервизор, администратор, ML-инженер, внешняя система | `test_auth.py::test_roles_limit_what_a_user_can_do` |
| Каждое изменение записано | журнал аудита: пользователь, действие, объект, код ответа, IP, агент | `test_auth.py::test_every_change_is_audited_with_user_ip_and_agent` |
| Решение привязано к учётной записи | `user_id` в решении инспектора, автор — из сессии, а не из запроса | `test_official_api.py` |

## Что решение не реализует

- Шифрование данных «в покое» средствами приложения: базы и копии лежат на
  томах `nadzor-state` и `nadzor-backups`, шифрование — средствами тома
  (LUKS или СХД заказчика). Передача — TLS 1.3 (порт 5443).
- Систему обнаружения вторжений и защиту от DDoS: это средства периметра
  контура заказчика; сервис даёт им журнал безопасности (`security.log`) и
  метрики.

- Проверку усиленной электронной подписи: поле `signature_status` из реестра
  сохраняется как есть.
- ГОСТ-TLS и подпись передаваемого пакета УКЭП: передача во внешнюю систему
  включается адресом приёма (`NADZOR_RIN_URL`, только внутри контура), сервис
  предъявляет клиентский сертификат стандартным TLS (`NADZOR_RIN_CLIENT_CERT`);
  криптография по ГОСТ — на СКЗИ заказчика перед приёмником.
