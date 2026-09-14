from pathlib import Path

import pytest

from app.inspector_memory import (
    active_lessons,
    add_lesson,
    lessons_prompt,
    master_playbook,
)


def test_master_playbook_persists_and_is_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NADZOR_BLIND_BENCHMARK", "0")
    path = tmp_path / "inspector_memory.sqlite3"
    first = "Compare quantity only after matching the same engineering entity."
    second = "Verify every target location independently before closing coverage."

    first_id = add_lesson(first, source_type="synthetic_curriculum", path=path)
    second_id = add_lesson(second, source_type="synthetic_curriculum", path=path)

    assert first_id != second_id
    assert add_lesson(first, source_type="synthetic_curriculum", path=path) == first_id
    assert len(active_lessons(limit=100, path=path)) == 2

    body = master_playbook(path=path)
    assert first in body
    assert second in body

    prompt = lessons_prompt(path=path)
    assert "MASTER PLAYBOOK" in prompt
    assert first in prompt
    assert second in prompt


def test_blind_mode_hides_all_learned_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "inspector_memory.sqlite3"
    monkeypatch.setenv("NADZOR_BLIND_BENCHMARK", "0")
    add_lesson(
        "Check connectivity separately from component presence.",
        source_type="synthetic_curriculum",
        path=path,
    )
    assert master_playbook(path=path)

    monkeypatch.setenv("NADZOR_BLIND_BENCHMARK", "1")
    assert active_lessons(limit=100, path=path) == []
    assert master_playbook(path=path) == ""
    assert lessons_prompt(path=path) == ""


def test_object_specific_lesson_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NADZOR_BLIND_BENCHMARK", "0")
    with pytest.raises(ValueError):
        add_lesson("Check room 267 for a missing element.", path=tmp_path / "memory.sqlite3")
