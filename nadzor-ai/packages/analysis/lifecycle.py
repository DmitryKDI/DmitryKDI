"""Модель состояний объекта и определение доступных переходов."""
from __future__ import annotations

from analysis.models import TRANSITIONS, DocumentSet
from documents.schemas import StateKind

# Какие состояния нужны переходу: (состояние «до», состояние «после»).
REQUIREMENTS: dict[str, tuple[StateKind | None, StateKind]] = {
    "T1": (StateKind.PD, StateKind.PD),
    "T2": (StateKind.PD, StateKind.EXPERTISE),
    "T3": (StateKind.PD, StateKind.RD),
    "T4": (StateKind.RD, StateKind.RD),
    "T5": (StateKind.RD, StateKind.AS_BUILT),
    "T6": (StateKind.AS_BUILT, StateKind.AS_BUILT),
    "T7": (None, StateKind.AS_BUILT),
}


def available_transitions(states: list[DocumentSet]) -> list[dict]:
    """Какие переходы доступны по составу загруженного комплекта."""
    by_kind: dict[StateKind, list[DocumentSet]] = {}
    for state in states:
        by_kind.setdefault(state.state_kind, []).append(state)

    result = []
    for code, (need_before, need_after) in REQUIREMENTS.items():
        title = TRANSITIONS[code][0]
        after = by_kind.get(need_after, [])
        if need_before is None:
            ok = bool(states)
            reason = "" if ok else "не загружено ни одного документа"
        elif need_before == need_after:
            ok = len(after) >= 2
            reason = "" if ok else f"нужно не менее двух редакций состояния {need_after.value}"
        else:
            before = by_kind.get(need_before, [])
            ok = bool(before) and bool(after)
            reason = "" if ok else _missing(before, after, need_before, need_after)
        result.append({"code": code, "title": title, "available": ok, "reason": reason})
    return result


def _missing(before: list, after: list, need_before: StateKind, need_after: StateKind) -> str:
    missing = []
    if not before:
        missing.append(need_before.value)
    if not after:
        missing.append(need_after.value)
    return "не загружено: " + ", ".join(missing)


def pick_states(states: list[DocumentSet],
                transition: str) -> tuple[DocumentSet | None, DocumentSet | None]:
    """Выбрать пару состояний для перехода.

    Для одинаковых видов состояний берутся две последние редакции.
    """
    need_before, need_after = REQUIREMENTS[transition]
    if need_before == need_after and need_before is not None:
        same = sorted([s for s in states if s.state_kind == need_before],
                      key=lambda s: _revision_key(s.revision))
        if len(same) < 2:
            return None, None
        return same[-2], same[-1]
    after = _latest(states, need_after)
    if need_before is None:
        return None, after
    return _latest(states, need_before), after


def _latest(states: list[DocumentSet], kind: StateKind) -> DocumentSet | None:
    """Последняя редакция состояния заданного вида."""
    same = [s for s in states if s.state_kind == kind]
    if not same:
        return None
    return max(same, key=lambda s: _revision_key(s.revision))


def _revision_key(revision: str) -> tuple[int, str]:
    digits = "".join(ch for ch in revision if ch.isdigit())
    return (int(digits) if digits else 0, revision)
