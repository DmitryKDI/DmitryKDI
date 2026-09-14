import os

from app import load_project_env


def test_project_env_loader_reads_values_without_overwriting_process_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "NADZOR_ENV_TEST_ONE=from_file\n"
        "NADZOR_ENV_TEST_TWO=\"quoted value\"\n"
        "export NADZOR_ENV_TEST_THREE=exported\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NADZOR_ENV_TEST_ONE", "process_wins")
    monkeypatch.delenv("NADZOR_ENV_TEST_TWO", raising=False)
    monkeypatch.delenv("NADZOR_ENV_TEST_THREE", raising=False)

    loaded = load_project_env(env_file)

    assert loaded == 2
    assert os.environ["NADZOR_ENV_TEST_ONE"] == "process_wins"
    assert os.environ["NADZOR_ENV_TEST_TWO"] == "quoted value"
    assert os.environ["NADZOR_ENV_TEST_THREE"] == "exported"


def test_project_env_loader_ignores_missing_and_malformed_files(tmp_path):
    missing = tmp_path / "missing.env"
    assert load_project_env(missing) == 0

    malformed = tmp_path / ".env"
    malformed.write_text("NO_EQUALS\n1BAD=value\n=empty\n", encoding="utf-8")
    assert load_project_env(malformed) == 0
