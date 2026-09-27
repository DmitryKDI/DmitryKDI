"""Схема API — OpenAPI 3.0 (ТЗ 1.3), без конструкций 3.1.

Полную валидацию выполняет openapi-spec-validator (проверено при сборке:
`make openapi`); здесь — дешёвая защита от возврата к конструкциям 3.1,
которые валидатор 3.0 отвергает.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import openapi30  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _walk(node, path=""):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}/{index}")


def test_served_schema_is_openapi_30_without_31_constructs():
    schema = TestClient(app).get("/openapi.json").json()
    assert schema["openapi"] == "3.0.3"
    for path, node in _walk(schema):
        assert node.get("type") != "null" or path.endswith("/example"), path
        assert not isinstance(node.get("type"), list), path
        assert "const" not in node and "contentMediaType" not in node, path
        assert not ("$ref" in node and len(node) > 1), path
    upload = schema["paths"]["/api/v1/documents/upload"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]


def test_nullable_reference_keeps_the_reference():
    converted = openapi30.convert({"anyOf": [{"$ref": "#/x"}, {"type": "null"}]})
    assert converted == {"allOf": [{"$ref": "#/x"}], "nullable": True}
    assert openapi30.convert({"anyOf": [{"type": "integer"}, {"type": "null"}],
                              "title": "T"}) == {"type": "integer", "title": "T",
                                                 "nullable": True}
