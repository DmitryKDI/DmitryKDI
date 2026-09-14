"""Синтетическое сравнение с HEAD; провайдер и реальные документы не используются.

Запуск: PYTHONPATH=.:packages/backend .venv/bin/python scripts/benchmark_candidate_selection.py
Заглушка отвечает без задержки; измеренное время не является временем провайдера.
"""
import json
import subprocess
import sys
import time
import types

from app.compliance import check_compliance
from app.requirement_registry import Requirement


def run():
    baseline = types.ModuleType("app._candidate_baseline")
    sys.modules[baseline.__name__] = baseline
    source = subprocess.check_output(
        ["git", "show", "HEAD:nadzor-ai/packages/backend/app/compliance.py"], text=True)
    exec(compile(source, "<HEAD compliance>", "exec"), baseline.__dict__)
    words = ["альфа", "бета", "гамма", "дельта", "эпсилон", "дзета"]
    index = {"101": [{"path": "synthetic.pdf", "page": i + 1, "text": word}
                     for i, word in enumerate(words)]}
    reqs = [Requirement(rooms=["101"], page=i + 1, sentence=f"Требуется {word}")
            for i, word in enumerate(words * 10)]
    output = {}
    for scenario in ("readable", "no_text"):
        current_index = {room: [{**e, "text": e["text"] if scenario == "readable" else ""}
                                for e in entries] for room, entries in index.items()}
        output[scenario] = {}
        for version, check in (("before", baseline.check_compliance), ("after", check_compliance)):
            calls = []
            def vision(path, page, sentence, rooms, config, calls=calls):
                calls.append((path, page, sentence))
                return {"verdict": "confirmed" if sentence.endswith(words[page - 1])
                        else "unclear"}
            kwargs = {"candidate_pages": lambda *a: [("synthetic.pdf", i + 1)
                                                      for i in range(len(words))]}
            if version == "after":
                kwargs = {"room_index": current_index}
            started = time.perf_counter()
            result = check(reqs, [], [], object(), llm_verify=lambda *a: [],
                           vision_check=vision, **kwargs)
            elapsed = time.perf_counter() - started
            output[scenario][version] = {
                "requirements": len(reqs), "calls": len(calls),
                "confirmed": result.counts.get("подтверждено", 0),
                "unique_viewed_pages": len({(p, n) for p, n, _ in calls}),
                "cpu_and_stub_seconds": round(elapsed, 6),
            }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
