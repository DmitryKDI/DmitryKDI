from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from entity_guided_retrieval import pd_anchors, priority_rd_context  # noqa: E402


def test_pd_anchors_prefer_specific_entities():
    anchors = pd_anchors([
        {"page": 1, "text": "Для установки AHU-Z91 предусмотреть клапан VLV-Z91 и расход 1250 м3/ч."}
    ])
    joined = " ".join(anchors).casefold()
    assert "ahu-z91" in joined
    assert "vlv-z91" in joined
    assert "1250" in joined


def test_priority_context_promotes_matching_rd_page():
    pd_pages = [{"page": 1, "text": "AHU-Z91 подключить к магистрали SUP-Z91 через VLV-Z91."}]
    fake_path = Path("synthetic-rd.pdf")

    def fake_extract(path: Path):
        assert path == fake_path
        return [
            {"page": 1, "text": "Общие указания. Посторонняя система без изменений."},
            {"page": 2, "text": "AHU-Z91 подключена к SUP-Z91 через VLV-Z91."},
        ]

    context = priority_rd_context(pd_pages, [fake_path], fake_extract, top_pages=1)
    assert "page 2" in context
    assert "AHU-Z91" in context
    assert "page 1" not in context
