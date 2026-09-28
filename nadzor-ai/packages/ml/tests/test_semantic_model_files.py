from __future__ import annotations

from pathlib import Path

from app import semantic


def _layout(root: Path, onnx_relative: str) -> Path:
    folder = root / semantic.DEFAULT_MODEL
    (folder / onnx_relative).parent.mkdir(parents=True, exist_ok=True)
    (folder / onnx_relative).write_bytes(b"onnx")
    (folder / "tokenizer.json").write_text("{}", encoding="utf-8")
    return folder


def test_onnx_in_model_root_is_found(tmp_path):
    folder = _layout(tmp_path, "model.onnx")
    assert semantic.model_files(folder) == (folder / "model.onnx", folder / "tokenizer.json")


def test_onnx_in_hugging_face_subfolder_is_found(tmp_path):
    # snapshot_download кладёт ONNX-выгрузку sentence-transformers в onnx/model.onnx
    folder = _layout(tmp_path, "onnx/model.onnx")
    assert semantic.model_files(folder) == (folder / "onnx" / "model.onnx", folder / "tokenizer.json")


def test_missing_files_report_the_folder(tmp_path):
    assert semantic.model_files(tmp_path / "absent") is None
