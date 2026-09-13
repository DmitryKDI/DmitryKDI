"""ОСТАНОВКА ПРОГОНА — кнопка «стоп» у каждого длинного дела (Г.114).

Разбор тома и сверка идут минутами и стоят вызовов модели. До этого
остановить запущенное было нельзя вообще: ошибся комплектом — жди конца
или перезапускай сервер. Перезапуск сервера при этом ничего не исправлял,
а делал хуже: работа обрывалась на середине, а запись прогона оставалась
в состоянии «идёт», и полоса прогресса показывала ход у мёртвого прогона.

Как устроено. Просьба остановиться — это флаг: она НЕ убивает поток и не
рвёт запрос к провайдеру на полуслове. Исполнитель сам проверяет флаг в
местах, где прерваться безопасно (между пачками, между требованиями,
между листами), и выходит, оставив запись в состоянии «остановлен
инспектором». Поэтому остановка не мгновенная — она наступает на
ближайшей развилке, и интерфейс говорит именно это, а не «остановлено»
раньше времени.

Флаг живёт в памяти процесса и дублируется в записи прогона (`cancelled_at`):
память нужна исполнителю, который крутится прямо сейчас, запись — чтобы
после перезапуска сервера остановленный прогон не запустился заново.
"""
from __future__ import annotations

import threading

# Виды прогонов. Строки, а не классы моделей: сюда не должно тянуться
# ничего из слоя базы, иначе модуль нельзя проверить без неё.
KIND_PD = "pd"
KIND_COMPLIANCE = "compliance"
KIND_ANALYSIS = "analysis"
KIND_TRIANGULATED = "triangulated"
KINDS = (KIND_PD, KIND_COMPLIANCE, KIND_ANALYSIS, KIND_TRIANGULATED)


class RunCancelled(Exception):
    """Прогон остановлен по просьбе инспектора — это не сбой."""


_lock = threading.Lock()
_requested: set[tuple[str, int]] = set()


def request(kind: str, run_id: int) -> None:
    with _lock:
        _requested.add((kind, int(run_id)))


def clear(kind: str, run_id: int) -> None:
    """Снять флаг — при запуске нового прогона с тем же номером."""
    with _lock:
        _requested.discard((kind, int(run_id)))


def is_requested(kind: str, run_id: int) -> bool:
    with _lock:
        return (kind, int(run_id)) in _requested


def check(kind: str, run_id: int) -> None:
    """Точка, где прерваться безопасно. Бросает RunCancelled, если просили."""
    if is_requested(kind, run_id):
        raise RunCancelled(f"{kind}#{run_id}")


def pending() -> set[tuple[str, int]]:
    with _lock:
        return set(_requested)
