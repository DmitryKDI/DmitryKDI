import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

from app.classification import PAGE_KIND_DRAWING, PAGE_KIND_TEXT, classify_page_kind

SAMPLE_DIR = Path(
    "/tmp/claude-0/-home-user-DmitryKDI/0870a421-62c2-59a8-8978-c9163f520b16/scratchpad"
)


def test_real_drawings_and_text_pages_classified_correctly():
    cases = [
        ("rd_floor1.pdf", 1, PAGE_KIND_DRAWING, "план вентиляции, A0"),
        ("rd_floor2_heating.pdf", 1, PAGE_KIND_DRAWING, "план отопления, A0"),
        ("pd_tom542_real_name_ОВ.pdf", 1, PAGE_KIND_TEXT, "титульный лист тома, A4"),
        ("pd_tom542_real_name_ОВ.pdf", 10, PAGE_KIND_TEXT, "содержательная страница тома, A4"),
        ("rd_aosr_ov1.pdf", 1, PAGE_KIND_TEXT, "акт АОСР (приложение), A4"),
    ]
    for name, page_no, expected, label in cases:
        doc = pymupdf.open(str(SAMPLE_DIR / name))
        got = classify_page_kind(doc[page_no - 1])
        doc.close()
        assert got == expected, f"{label} ({name} p{page_no}): got {got}, expected {expected}"
    print("OK: real drawing sheets and real text/appendix pages classified correctly by format+vector density")


if __name__ == "__main__":
    test_real_drawings_and_text_pages_classified_correctly()
    print("ALL PASS")
