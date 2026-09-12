"""Ask the configured GigaChat model how it wants the drawing-comparison prompt structured.

This is a developer diagnostic only. It does not receive benchmark ground truth,
expected room numbers or known violations. The script uses the same credentials and
provider code as the backend, then writes a reusable transcript to run_logs/.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "packages" / "backend"
sys.path.insert(0, str(BACKEND))

from app.llm import LlmConfig, call_llm_json, credentials_from_file  # noqa: E402


SYSTEM_PROMPT = """Ты выступаешь как технический консультант разработчиков системы анализа
строительных чертежей. Нужна критика именно того, как лучше давать инструкции
тебе как vision-модели для сравнения ПД и РД/ИД.

Важно: не пытайся угадывать ожидаемые нарушения. Не проси и не используй
эталонные ответы, номера помещений из benchmark или исторические known cases.
Нас интересует универсальная механика для любых комплектов документов.

Отвечай предметно: что помогает тебе реально увидеть инженерное изменение,
что вызывает false negative, какой размер/компоновка изображений лучше и какой
контракт ответа снижает риск преждевременного `no change`.

Не давай юридических оценок. На этом этапе задача только визуально зафиксировать
различия инженерных решений.

Верни только JSON."""


USER_PROMPT = """Мы строим blind-пайплайн сравнения ПД -> РД/ИД.

Алгоритмический слой уже делает следующее:
- находит кандидатные пары листов;
- сохраняет raster-diff даже если vision ничего не подтвердил;
- выделяет общие ROOM-якоря;
- при сложном листе делает room-focused crop;
- может собрать один монтаж, где в каждой строке слева ПД, справа РД/ИД.

Проблема: на плотных инженерных чертежах vision иногда даёт false negative или
цепляется за архитектурную подпись вместо инженерного слоя.

Ответь как модель, которая сама будет выполнять эту задачу:
1) какой layout надёжнее: два изображения ПД/РД или один paired-монтаж;
2) сколько ROOM разумно показывать за один вызов;
3) какой minimum crop вокруг ROOM нужен, чтобы не потерять трассы/подключения;
4) какие формулировки инструкции чаще всего заставляют тебя завершать анализ слишком рано;
5) какой пошаговый алгоритм проверки ROOM лучше требовать от модели;
6) как отличать `same` от `unclear`, чтобы нечитаемый фрагмент не стал false negative;
7) какой JSON-контракт ты считаешь наиболее надёжным;
8) напиши рекомендуемый system prompt целиком.

Нас особенно интересуют вентиляция, отопление, трубопроводы, оборудование,
воздуховоды, решётки, клапаны, стояки, трассы и подключения. Название помещения,
мебель, стены, рамки и штампы сами по себе не являются инженерным отличием.

Верни JSON следующего вида:
{
  "preferred_layout": "...",
  "rooms_per_call": {"recommended": 0, "max": 0, "reason": "..."},
  "crop_guidance": "...",
  "false_negative_causes": ["..."],
  "room_check_sequence": ["..."],
  "same_vs_unclear_rule": "...",
  "recommended_contract": {...},
  "recommended_system_prompt": "...",
  "extra_notes": ["..."]
}"""


def _load_dotenv(path: Path) -> None:
    """Load the project's simple KEY=VALUE .env without adding a dependency."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _credentials() -> str:
    _load_dotenv(ROOT / ".env")

    direct = os.environ.get("GIGACHAT_CREDENTIALS", "").strip()
    if direct:
        return direct

    client_id = os.environ.get("GIGACHAT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GIGACHAT_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    return credentials_from_file("gigachat")


def main() -> int:
    credentials = _credentials()
    if not credentials:
        print(
            "GigaChat credentials not found. Configure nadzor-ai/.env with "
            "GIGACHAT_CLIENT_ID + GIGACHAT_CLIENT_SECRET or GIGACHAT_CREDENTIALS, "
            "or put a gigachat key file into nadzor-ai/secrets/.",
            file=sys.stderr,
        )
        return 2

    config = LlmConfig(provider="gigachat", api_key=credentials)
    result = call_llm_json(
        config,
        SYSTEM_PROMPT,
        USER_PROMPT,
        operation="text_verify",
        source_digest="gigachat-peer-review-blind-v1",
        prompt_version="gigachat-peer-review-v1",
        use_cache=False,
    )
    if not isinstance(result, dict):
        print("GigaChat did not return a JSON object.", file=sys.stderr)
        return 3

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": config.resolved_model(),
        "blind": True,
        "response": result,
    }
    out_dir = ROOT / "run_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "gigachat_peer_review_latest.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
