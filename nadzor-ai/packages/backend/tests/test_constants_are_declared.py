"""Каждая числовая константа механики объявлена и объяснена (Г.108).

Прямой вопрос пользователя: «если бы я тебе сейчас 100 томов кинул, ты бы
чисто под них всё писал?». Ответ по факту был «да, отчасти»: в механике
нашлось 53 числовые константы уровня модуля, и часть из них — пороги,
измеренные на одном-двух реальных томах. На сотне томов такой порог
ошибается там, где его никто не мерил, и подстроить его может только автор
кода — то есть программа не становится лучше от эксплуатации.

Разделение, которое эта проверка навязывает:

  БЮДЖЕТ            — сколько работы делать (страниц просмотреть, попыток
                      повторить, листов показать). Ошибка бюджета стоит
                      времени, а не правды. Константа допустима.
  ПРАВИЛО ЯЗЫКА     — свойство русского языка или формы документа по ГОСТ
                      (длина значимого слова, длина общего корня). Одинаково
                      для любого объекта и любого раздела.
  ГЕОМЕТРИЯ/ФОРМАТ  — свойство листа и единиц измерения (доли зоны штампа,
                      размер формата в мм, масштаб рендера).
  ПОДОГНАНО         — порог, выведенный из наблюдения на конкретных
                      документах. Допустим ТОЛЬКО с честным n и объяснением,
                      почему его нельзя вывести из данных прогона.
  ИЗ ДАННЫХ         — значение вычисляется по разбираемому документу или
                      берётся из накопленной базы, а не записано числом.

Проверка не запрещает подогнанные пороги — запрещает НЕЗАМЕТНЫЕ: новая
константа обязана быть объявлена здесь вместе с видом, иначе тест падает.
Так «подгонка под пример» перестаёт быть незаметной для самого автора,
чего не обеспечило ни одно словесное правило (Г.88, Г.89, Г.102, Г.104).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

APP = Path(__file__).resolve().parents[1] / "app"

BUDGET = "бюджет"
LANGUAGE = "правило языка"
GEOMETRY = "геометрия/формат"
FITTED = "подогнано"

# Вид каждой константы механики. Значение — вид; для ПОДОГНАНО обязательно
# добавлено, на скольких документах она измерена.
DECLARED: dict[str, str] = {
    # --- бюджеты: сколько работы, а не что считать правдой ---
    "classification.MAX_STAMP_SCAN_PAGES": BUDGET,
    "classification.TITLE_PAGE_MAX_BLOCKS": BUDGET,
    "classification.STAGE_SCAN_PAGES": BUDGET,
    # Сколько примеров показывать в выжимке и какими кусками читать файл ради
    # отпечатка: сколько работы и сколько строк читается взглядом, а не что
    # считать правдой. Версия разборщика — тоже не порог: это номер, по
    # которому прежний разбор перестаёт подходить (Г.114).
    "facts_store.FACTS_VERSION": BUDGET,
    "facts_store._CHUNK": BUDGET,
    "file_store.SQLITE_BUSY_TIMEOUT_SEC": BUDGET,
    "file_store.SQLITE_BUSY_TIMEOUT_MS": BUDGET,
    "document_split.DEFAULT_PART_BYTES": BUDGET,
    "diffing.MAX_DIFF_WORDS": BUDGET,
    "llm._RATE_LIMIT_MAX_RETRIES": BUDGET,
    "llm._REACH_TIMEOUT": BUDGET,
    "llm._REACH_ATTEMPTS": BUDGET,
    "llm._REACH_RETRY_DELAY": BUDGET,
    "llm._RATE_LIMIT_BASE_DELAY": BUDGET,
    "llm._RATE_LIMIT_MAX_DELAY": BUDGET,

    # --- бюджеты, заданные через окружение ------------------------------
    # Сколько чертёжных листов распознавать графическим OCR: лист сверх
    # бюджета помечается пропущенным, а не прочитанным.
    "documents.GRAPHIC_OCR_PAGE_BUDGET": BUDGET,
    # Разрешение растеризации для локального распознавания: мелкие подписи
    # чертежа при меньшем разрешении читаются хуже, больше — дольше без
    # выигрыша. Свойство формата изображения, а не наблюдение о документе.
    "local_ocr.OCR_DPI": GEOMETRY,
    # Минимум букв подряд, делающий фрагмент словом, — свойство языка: на
    # этом отличается распознанный текст от распознанного шума.
    "local_ocr.MIN_WORD_LETTERS": LANGUAGE,
    # Лимиты загрузки и число повторов при сбое заданы ТЗ (9.1) — это
    # бюджет приёма и работы, а не порог истины о документе.
    "api_v1.MAX_FILE_BYTES": BUDGET,
    "external_sync.SEND_TIMEOUT_S": BUDGET,
    # Политика доступа (ТЗ 12): срок сессии, длина пароля, стоимость scrypt.
    "auth.SESSION_TTL_HOURS": BUDGET,
    "auth.MIN_PASSWORD_LENGTH": BUDGET,
    "auth.SCRYPT_N": BUDGET,
    "auth.SCRYPT_R": BUDGET,
    "auth.SCRYPT_P": BUDGET,
    "api_v1.MAX_PACKAGE_BYTES": BUDGET,
    "official_api.EXECUTE_RETRIES": BUDGET,
    # Фоновые задачи: как часто проверять сроки и каким периодом строить отчёт.
    "jobs.POLL_S": BUDGET,
    # Оценка по эталону (ТЗ 14.3): порог IoU — из ТЗ; z — квантиль нормального
    # распределения для 95%; бутстреп — сколько повторов и с каким зерном.
    "evaluation.IOU_THRESHOLD": BUDGET,
    "evaluation.Z_95": LANGUAGE,
    "evaluation.BOOTSTRAP_ROUNDS": BUDGET,
    "evaluation.BOOTSTRAP_SEED": BUDGET,
    # Реестр XLSX: сколько распаковывать одной части — защита от ZIP-бомбы.
    "registry_xlsx.MAX_PART_BYTES": BUDGET,
    # Эксплуатация (ТЗ 12–13): сроки хранения логов и копий, периоды проверок —
    # значения из ТЗ; размеры кусков и тайм-ауты — бюджет.
    "observability.LOG_RETENTION_DAYS": BUDGET,
    "observability.SECURITY_LOG_RETENTION_DAYS": BUDGET,
    "integrity.CHECK_PERIOD_HOURS": BUDGET,
    "backup.FREQUENT_MINUTES": BUDGET,
    "backup.FREQUENT_KEEP_HOURS": BUDGET,
    "backup.DAILY_KEEP_DAYS": BUDGET,
    "antivirus.DEFAULT_PORT": GEOMETRY,
    "antivirus.TIMEOUT_S": BUDGET,
    "antivirus.CHUNK": BUDGET,
    "jobs.REPORT_PERIOD_DAYS": BUDGET,
    # Доли разбиения объектов по наборам — сколько данных отдать на обучение и
    # проверку, а не порог истины.
    "feedback.SPLIT_TRAIN_PERCENT": BUDGET,
    "feedback.SPLIT_VALIDATION_PERCENT": BUDGET,
    # Пороги приёмки модели — значения из ТЗ 14.3 и 9.4, а не подобранные.
    "feedback.MAX_FALSE_POSITIVE_RATE": BUDGET,
    "feedback.MAX_RECALL_DROP": BUDGET,
    "feedback.MAX_FPR_GROWTH": BUDGET,
    # Свободный поиск (ТЗ 9.5): значимое слово и общий корень — правило языка;
    # правило трёх сигм и минимум объектов для сравнения — общепринятая
    # статистика и объём выборки, а не порог, подобранный по примеру.
    "suspicions.SIGNIFICANT_WORD_LETTERS": LANGUAGE,
    "suspicions.COMMON_STEM_LETTERS": LANGUAGE,
    "suspicions.MIN_HISTORY_OBJECTS": BUDGET,
    "suspicions.ANOMALY_SIGMAS": BUDGET,
    # Все одного устройства: число в коде — умолчание, переменная окружения
    # — способ изменить его на месте, не пересобирая программу. Вид от
    # этого не меняется: ошибка такого числа стоит времени и денег за
    # вызовы, но не превращается в ложный вывод о документе.
    #
    # Сколько кандидатных листов РД оставлять на один лист ПД: чем слабее
    # признак совпадения, тем менее известно, который лист верный.
    # Сколько пар листов и увеличений разрешено за прогон.
    # Нижняя граница работы, а не истины: сколько местных проверок обязано
    # быть сделано, ПРЕЖДЕ чем разрешено записать «различий нет». Тоже
    # бюджет — говорит, сколько работать, — но направлен в другую сторону:
    # занизив его, платишь не временем, а поспешным выводом, поэтому
    # уменьшать эти два числа опаснее остальных.
    # Сколько вызовов зрения по зонам и по помещениям разрешено.
    # Сколько ходов, страниц и текста отдавать модели за один шаг разбора.
    "stateful_investigator.MAX_TURNS_CAP": BUDGET,
    "stateful_investigator.MIN_TURNS": BUDGET,
    "stateful_investigator.MAX_PAGES": BUDGET,
    "stateful_investigator.MAX_TEXT_PAGES": BUDGET,
    "stateful_investigator.MAX_REGIONS": BUDGET,
    "stateful_investigator.MAX_VERIFY_REFS": BUDGET,
    "stateful_investigator.PAGE_HINT_CHARS": BUDGET,
    "stateful_investigator.SEARCH_TEXT_CHARS": BUDGET,
    "stateful_investigator.PAGE_TEXT_CHARS": BUDGET,
    "conversation_llm.HISTORY_MAX_CHARS": BUDGET,
    "conversation_llm.CONVERSATION_RETRIES": BUDGET,
    "conversation_llm.NAV_ONLY_LIMIT": BUDGET,

    # --- геометрия рендера, заданная через окружение --------------------
    # Наибольшая сторона изображения в точках: свойство формата и
    # читаемости картинки, а не наблюдение о конкретном документе.
    "vision.VISION_MAX_DIM": BUDGET,
    "semantic_contract.REGIONS_PER_CALL": BUDGET,
    "llm_runtime.DEFAULT_CACHE_ENTRIES": BUDGET,
    "llm_runtime.DEFAULT_CLASSIFICATION_TOKENS": BUDGET,
    "llm_runtime.DEFAULT_CONCURRENCY": BUDGET,
    "llm_runtime.DEFAULT_EXTRACTION_TOKENS": BUDGET,
    "llm_runtime.DEFAULT_TEXT_VERIFY_TOKENS": BUDGET,
    "llm_runtime.DEFAULT_VISION_TOKENS": BUDGET,
    "llm_runtime.DEFAULT_WAIT_BUDGET": BUDGET,
    # Число строк задано официальной Матрицей 1.1, порог IoU — официальным
    # правилом приёмки геометрии доказательства, а не наблюдением на объекте.
    "parameter_catalog.PARAMETER_COUNT": GEOMETRY,

    # --- свойства языка и формы документа ---
    "requirement_registry._ROOM_KEYWORD_MIN_WORD_LEN": LANGUAGE,
    "requirement_registry._ROOM_KEYWORD_MIN_PREFIX": LANGUAGE,
    "diffing.MIN_PARAGRAPH_WORDS": LANGUAGE,
    "equipment._MAX_NAME_LINES": LANGUAGE,
    "rooms._MAX_NAME_LINES": LANGUAGE,
    "stamp._NAME_CONTINUATION_LIMIT": LANGUAGE,
    "subsystem._MARGIN": LANGUAGE,

    # --- геометрия листа и единицы измерения ---
    "classification.DRAWING_FORMAT_LONG_SIDE_MM": GEOMETRY,
    "stamp.STAMP_LEFT": GEOMETRY,
    "stamp.STAMP_TOP": GEOMETRY,
    "semantic_contract.BBOX_AREA_MIN": GEOMETRY,
    "semantic_contract.BBOX_AREA_MAX": GEOMETRY,
    "semantic_contract.BBOX_MAX_OVERLAP": GEOMETRY,

    # --- ПОДОГНАНО под наблюдение: честный n и почему не выводится ---
    "classification.MIN_CODE_SCORE": f"{FITTED}, n=1",
    "classification.DRAWING_MIN_VECTOR_PATHS": f"{FITTED}, n=1",
    "material._MIN_CATALOG_PAGES": f"{FITTED}, n=1",
    "material._MIN_PRICE_TOKENS": f"{FITTED}, n=1",
    "matching.MIN_PAGE_MATCH_SIMILARITY": f"{FITTED}, n=1",
    "matching.SUBSYSTEM_MISMATCH_PENALTY": f"{FITTED}, n=1",
    "matching.CONFIDENT_PAGE_MATCH_SIMILARITY": f"{FITTED}, n=1",
    "matching.SUBSYSTEM_MATCH_BONUS": f"{FITTED}, n=1",
    "matching.ROOM_ANCHOR_FLOOR": f"{FITTED}, n=1",
    "matching.EQUIPMENT_ANCHOR_FLOOR": f"{FITTED}, n=1",
    "diffing.MIN_SIMILARITY": f"{FITTED}, n=1",
    "diffing.MAX_SIMILARITY": f"{FITTED}, n=1",
    "semantic_contract.SAFE_COVERAGE_MIN": f"{FITTED}, n=1",
}

_CONST_RE = re.compile(r"^([A-Z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*[-+]?\d")
# Число, спрятанное за чтением окружения, — такая же константа механики.
# Проверка искала только литеральное присваивание, и 35 бюджетов вида
# `NAME = _int_env("ПЕРЕМЕННАЯ", 40, 0, 400)` прошли мимо неё незаметно —
# ровно тем способом, против которого Г.108 её и заводил: число стоит в
# коде, вид его никем не назван. Возможность переопределить значение
# переменной окружения не отменяет вопроса «из чего взято 40».
_ENV_CONST_RE = re.compile(
    r"^([A-Z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*(?:_int_env|positive_env)\s*\(")


def _module_constants() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(APP.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _CONST_RE.match(line) or _ENV_CONST_RE.match(line)
            if match:
                found[f"{path.stem}.{match.group(1)}"] = number
    return found


def test_every_numeric_constant_of_the_mechanics_is_declared():
    """Новая числовая константа обязана быть объявлена вместе с видом."""
    found = _module_constants()
    undeclared = sorted(name for name in found if name not in DECLARED)
    assert not undeclared, (
        "Числовые константы механики не объявлены в реестре видов.\n"
        "Каждая обязана быть либо бюджетом, либо свойством языка/формата,\n"
        "либо честно помеченной подгонкой с числом документов:\n  "
        + "\n  ".join(f"{n} (стр.{found[n]})" for n in undeclared))
    print(f"OK: все {len(found)} числовых констант механики объявлены")


def test_declared_constants_still_exist():
    """Обратная сторона: запись реестра, потерявшая свою константу, вводит
    в заблуждение не меньше незаявленной константы."""
    found = _module_constants()
    stale = sorted(name for name in DECLARED if name not in found)
    assert not stale, ("В реестре остались записи без констант:\n  " + "\n  ".join(stale))
    print("OK: в реестре нет записей об исчезнувших константах")


def test_fitted_thresholds_declare_how_many_documents_they_were_measured_on():
    """Подгонка допустима, безымянная подгонка — нет: у каждой обязан быть
    честный n, чтобы следующий читатель видел границу применимости (Г.21)."""
    bad = sorted(name for name, kind in DECLARED.items()
                 if kind.startswith(FITTED) and not re.search(r"n=\d+", kind))
    assert not bad, ("Подогнанные пороги без числа документов:\n  " + "\n  ".join(bad))
    fitted = [n for n, k in DECLARED.items() if k.startswith(FITTED)]
    print(f"OK: {len(fitted)} подогнанных порогов объявлены с честным n; "
          f"остальные {len(DECLARED) - len(fitted)} — бюджеты, язык и геометрия")


if __name__ == "__main__":
    test_every_numeric_constant_of_the_mechanics_is_declared()
    test_declared_constants_still_exist()
    test_fitted_thresholds_declare_how_many_documents_they_were_measured_on()
    print("ALL PASS")
