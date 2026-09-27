"""Схема API в формате OpenAPI 3.0 (ТЗ 1.3).

FastAPI строит схему в OpenAPI 3.1, а ТЗ требует валидации по OpenAPI 3.0.
Версии несовместимы в нескольких конструкциях, и заменить номер версии в
заголовке недостаточно — валидатор 3.0 отвергнет схему. Здесь те же
описания переписываются конструкциями 3.0:

  * `anyOf` с `{"type": "null"}` и `type: [X, "null"]` → `nullable: true`;
  * `const` → `enum` из одного значения;
  * `examples` в схеме (список) → `example`;
  * файл (`contentMediaType`) → `format: binary`;
  * числовые `exclusiveMinimum/Maximum` → `minimum/maximum` + флаг.
"""
from __future__ import annotations

import copy

OPENAPI_VERSION = "3.0.3"
_NULL = {"type": "null"}


def _schema(node: dict) -> dict:
    node = {key: convert(value) for key, value in node.items() if key != "$schema"}
    for key in ("anyOf", "oneOf"):
        variants = node.get(key)
        if isinstance(variants, list) and _NULL in variants:
            rest = [variant for variant in variants if variant != _NULL]
            node.pop(key)
            if len(rest) == 1:
                merged = {**rest[0], **node, "nullable": True}
                if "$ref" in merged:
                    # Рядом с $ref в 3.0 прочие ключи игнорируются: nullable
                    # сохраняется через allOf.
                    ref = merged.pop("$ref")
                    merged["allOf"] = [{"$ref": ref}]
                node = merged
            else:
                node = {**node, key: rest, "nullable": True}
    kind = node.get("type")
    if isinstance(kind, list):
        types = [item for item in kind if item != "null"]
        node["type"] = types[0] if types else "string"
        if "null" in kind:
            node["nullable"] = True
    if "const" in node:
        node["enum"] = [node.pop("const")]
    if isinstance(node.get("examples"), list):
        examples = node.pop("examples")
        if examples:
            node["example"] = examples[0]
    if node.pop("contentMediaType", None) is not None:
        node["format"] = "binary"
    for bound, plain in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        value = node.get(bound)
        if isinstance(value, int | float) and not isinstance(value, bool):
            node[plain] = value
            node[bound] = True
    return node


def convert(node):
    if isinstance(node, dict):
        return _schema(node)
    if isinstance(node, list):
        return [convert(item) for item in node]
    return node


def downgrade(schema: dict) -> dict:
    """Схема 3.1 от FastAPI → равнозначная схема OpenAPI 3.0."""
    result = convert(copy.deepcopy(schema))
    result["openapi"] = OPENAPI_VERSION
    return result


def install(app) -> None:
    """Подменяет app.openapi: /openapi.json и /docs отдают схему 3.0."""
    original = app.openapi

    def openapi() -> dict:
        if app.openapi_schema is None or app.openapi_schema.get("openapi") != OPENAPI_VERSION:
            app.openapi_schema = downgrade(original())
        return app.openapi_schema

    app.openapi = openapi
