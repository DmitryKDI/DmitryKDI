import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.registry_diff import _diff, _registry  # noqa: E402


def test_diff_splits_into_three_categories():
    before = {"101": [], "102": [], "103": []}
    after = {"102": [], "103": [], "104": []}
    both, only_before, only_after = _diff(before, after)
    assert both == {"102", "103"}
    assert only_before == {"101"}
    assert only_after == {"104"}
    print("OK: три категории (Г.9) считаются чистым пересечением/разностью множеств")


def test_diff_empty_sides_do_not_crash():
    both, only_before, only_after = _diff({}, {})
    assert both == only_before == only_after == set()
    print("OK: пустые реестры не падают, дают пустые категории")


def test_registry_skips_missing_file_without_crashing(capsys):
    """Битый/отсутствующий файл не должен ронять весь прогон — реальная
    ситуация: неверный путь, файл переименован между запусками."""
    reg = _registry(["/nonexistent/path/does-not-exist.pdf"], "room_facts")
    assert reg == {}
    captured = capsys.readouterr()
    assert "не найден" in captured.err
    print("OK: отсутствующий файл пропускается с предупреждением, не падает")


if __name__ == "__main__":
    test_diff_splits_into_three_categories()
    test_diff_empty_sides_do_not_crash()
    print("ALL PASS (запустите pytest для теста с capsys)")
