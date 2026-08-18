#!/usr/bin/env python3
"""Прогон анализа по демонстрационному комплекту (без веб-слоя).

Используется скриптом метрик и для быстрой проверки детектора из командной строки.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

import yaml  # noqa: E402
from analysis.runner import object_card_for_rules, parse_documents, run_analysis  # noqa: E402
from llm_core.router import ProviderRouter  # noqa: E402
from norms import load_norms  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ALL_TRANSITIONS = ["T1", "T2", "T3", "T5", "T6"]


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


async def run_object(object_id: str, manifest_path: Path | None = None) -> dict:
    """Разобрать комплект объекта и выполнить все доступные переходы."""
    manifest_path = manifest_path or ROOT / "data/demo/generated/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    obj = next(o for o in manifest["objects"] if o["id"] == object_id)
    entries = [m for m in manifest["documents"] if m["object_id"] == object_id]

    entries_paths = [(ROOT / e["file"], e["title"]) for e in entries]
    docs = parse_documents(entries_paths, object_id)

    router = ProviderRouter.from_file(ROOT / "config/providers.yaml")
    provider = router.select(protected_object=obj.get("protected", False))
    result = await run_analysis(
        object_id, docs, object_card_for_rules(obj),
        load_norms(ROOT / "data/norms/norms.json", ROOT / "data/norms/classifier.json"),
        _load_yaml("rules.yaml"), _load_yaml("scoring.yaml"), provider,
        router.verifier(provider, obj.get("protected", False)),
        run_id=f"run-{object_id}", transitions=ALL_TRANSITIONS)
    return {"object": obj, **result}


def main() -> None:
    object_id = sys.argv[1] if len(sys.argv) > 1 else "OBJ-001"
    result = asyncio.run(run_object(object_id))
    print(f"Объект {object_id}: состояний {len(result['states'])}, "
          f"документов {sum(len(s.docs) for s in result['states'])}, "
          f"находок {len(result['findings'])}")
    for state in result["states"]:
        print(f"  состояние {state.state_kind.value:<10} ред. {state.revision:<3} "
              f"док. {len(state.docs):>2}  фактов {len(state.all_facts()):>3}  {state.label}")
    print()
    for f in result["findings"]:
        print(f"  [{f.transition}] {f.code} {f.severity:<8} балл {f.priority_score:>5} "
              f"conf {f.confidence:.2f}  {f.title[:96]}")
    print("\nКарта внимания:")
    for item in result["attention"]:
        print(f"  {item['priority_score']:>5}  {item['location'][:44]:<44} {item['summary'][:70]}")


if __name__ == "__main__":
    main()
