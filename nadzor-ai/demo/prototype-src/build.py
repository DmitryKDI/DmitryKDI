import json, pathlib
SP = pathlib.Path("/tmp/claude-0/-home-user-DmitryKDI/0870a421-62c2-59a8-8978-c9163f520b16/scratchpad")
P, REPO = SP/"proto", pathlib.Path("/home/user/DmitryKDI/nadzor-ai")
data = json.loads((SP/"demo.json").read_text(encoding="utf-8"))
sheets = json.loads((SP/"pages.json").read_text(encoding="utf-8"))
classifier = json.loads((REPO/"data/norms/classifier.json").read_text(encoding="utf-8"))["classes"]
app = {"objects": data["objects"], "findings": data["findings"],
       "documents": {k: [{kk: d[kk] for kk in ("title","kind","state","revision","date","pages","facts")}
                         for d in v] for k, v in data["pages"].items()},
       "classifier": classifier}
html = (P/"head.html").read_text(encoding="utf-8") + (P/"body.html").read_text(encoding="utf-8")
html += '\n<script id="appdata" type="application/json">' + json.dumps(app, ensure_ascii=False, separators=(",",":")) + '</script>\n'
html += '<script id="appsheets" type="application/json">' + json.dumps(sheets, separators=(",",":")) + '</script>\n'
html += '<script>\n' + "\n".join((P/f"app{i}.js").read_text(encoding="utf-8") for i in range(1,6)) + '\n</script>\n'
(SP/"nadzor-prototype.html").write_text(html, encoding="utf-8")
print("собрано:", round(len(html.encode())/1024/1024, 2), "МБ")
