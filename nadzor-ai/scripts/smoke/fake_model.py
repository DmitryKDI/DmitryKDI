"""Дублёр модели для проверки запуска без видеокарты.

Отвечает по тому же протоколу, что сервер модели в контуре (OpenAI-совместимый
vLLM): /health, /v1/models, /v1/chat/completions. Нужен, чтобы проверить весь
путь — загрузку, разбор, сверку, протокол, решения, финализацию и выгрузку —
на машине, где нет GPU и весов. Качество модели он НЕ проверяет и не
изображает: ответы строятся механически из переданного текста.

Правила ответа на сверку по матрице — честные и воспроизводимые:
  * строка документа считается относящейся к параметру, если содержит два
    первых значимых слова его названия;
  * цитата — эта строка дословно, поэтому код может проверить её
    происхождение и найти координаты на странице;
  * кандидат объявляется, только если числа в строках ПД и РД/ИД различаются;
    совпали — «расхождения нет»; строки нет с одной из сторон — «данных
    недостаточно».
Любой другой запрос (графический исследователь, чтение штампа) получает
ответ «работа закончена» или пустой объект.

Только стандартная библиотека: запускается и на хосте, и в любом образе.

    python scripts/smoke/fake_model.py --port 8001 --model smoke-model
"""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PAGE_RE = re.compile(r'<page stage="(PD|RD|ID)" document_id="(\d+)" page="(\d+)">(.*?)</page>',
                      re.DOTALL)
_WORD_RE = re.compile(r"[^\W\d_]{5,}")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _key_words(name: str) -> list[str]:
    return [word.casefold() for word in _WORD_RE.findall(name)][:2]


def _find_line(pages: list[tuple], stages: set[str], words: list[str]) -> tuple | None:
    for stage, document_id, page, text in pages:
        if stage not in stages:
            continue
        for line in text.splitlines():
            folded = line.casefold()
            if words and all(word in folded for word in words):
                return stage, int(document_id), int(page), line.strip()
    return None


def _matrix_answer(user: str) -> dict:
    params_start = user.index("Параметры:") + len("Параметры:")
    params_end = user.index("\nДокументы:", params_start)
    parameters = json.loads(user[params_start:params_end].strip())
    pages = _PAGE_RE.findall(user)
    checks = []
    for parameter in parameters:
        words = _key_words(str(parameter.get("name") or ""))
        expected = _find_line(pages, {"PD"}, words)
        actual = _find_line(pages, {"RD", "ID"}, words)
        if expected is None or actual is None:
            checks.append({"parameter_code": parameter["code"],
                           "assessment": "INSUFFICIENT_EVIDENCE", "evidence": [],
                           "explanation": "дублёр: строка параметра найдена не на обеих стадиях"})
            continue
        left = _NUMBER_RE.findall(expected[3])
        right = _NUMBER_RE.findall(actual[3])
        differs = left != right
        checks.append({
            "parameter_code": parameter["code"],
            "assessment": "CANDIDATE" if differs else "NO_DIFFERENCE_OBSERVED",
            "expected_value": " ".join(left) or expected[3],
            "actual_value": " ".join(right) or actual[3],
            "explanation": ("дублёр: значения в ПД и фактической стадии различаются"
                            if differs else "дублёр: значения совпадают"),
            "confidence": 0.5,
            "approved_change_ref": "NONE",
            "evidence": [
                {"stage": expected[0], "document_id": expected[1], "page": expected[2],
                 "quote": expected[3]},
                {"stage": actual[0], "document_id": actual[1], "page": actual[2],
                 "quote": actual[3]},
            ],
        })
    return {"checks": checks}


def answer(system: str, user: str) -> dict:
    if 'Ответь ровно: {"ok": true}' in user:
        return {"ok": True}
    if "Параметры:" in user and "\nДокументы:" in user:
        return _matrix_answer(user)
    if '"action"' in system:
        return {"action": "finish", "summary": "дублёр модели: графика не анализировалась",
                "requirements_checked": [], "unresolved": []}
    return {}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


class Handler(BaseHTTPRequestHandler):
    model = "smoke-model"

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — имя задано http.server
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": self.model}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        messages = request.get("messages") or []
        system = next((_text(m.get("content")) for m in messages if m.get("role") == "system"), "")
        user = "\n".join(_text(m.get("content")) for m in messages if m.get("role") == "user")
        content = json.dumps(answer(system, user), ensure_ascii=False)
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content},
                                      "finish_reason": "stop"}]})

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--model", default="smoke-model")
    args = parser.parse_args()
    Handler.model = args.model
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
