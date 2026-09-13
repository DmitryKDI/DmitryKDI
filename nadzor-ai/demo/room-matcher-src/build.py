"""Собрать room-matcher.html: подставить app.js/main.js и вшить pdf.js как base64.

pdf.min.mjs и pdf.worker.min.mjs не хранятся в репозитории (стороннее
собранное дерево, ~1.7 МБ) — получите их один раз командой:

    npm install --no-save pdfjs-dist@4
    cp node_modules/pdfjs-dist/build/pdf.min.mjs demo/room-matcher-src/
    cp node_modules/pdfjs-dist/build/pdf.worker.min.mjs demo/room-matcher-src/

Дальше просто: python3 demo/room-matcher-src/build.py
"""
import base64
import sys
from pathlib import Path

HERE = Path(__file__).parent
LIB = HERE / "pdf.min.mjs"
WORKER = HERE / "pdf.worker.min.mjs"

if not LIB.exists() or not WORKER.exists():
    sys.exit(
        "Не найдены pdf.min.mjs / pdf.worker.min.mjs рядом со скриптом.\n"
        "Получите их: npm install --no-save pdfjs-dist@4 и скопируйте из "
        "node_modules/pdfjs-dist/build/ — см. docstring этого файла."
    )

template = (HERE / "app_template.html").read_text(encoding="utf-8")
lib_b64 = base64.b64encode(LIB.read_bytes()).decode("ascii")
worker_b64 = base64.b64encode(WORKER.read_bytes()).decode("ascii")
app_js = (HERE / "app.js").read_text(encoding="utf-8")
main_js = (HERE / "main.js").read_text(encoding="utf-8")

out = template.replace("__PDFJS_LIB_B64__", lib_b64)
out = out.replace("__PDFJS_WORKER_B64__", worker_b64)
out = out.replace("__APP_JS__", app_js + "\n\n" + main_js)

output_path = HERE.parent / "room-matcher.html"
output_path.write_text(out, encoding="utf-8")
print("bytes:", len(out.encode("utf-8")), "->", output_path)
