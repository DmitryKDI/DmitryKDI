import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.material import non_project_reason


def test_supplier_catalog_page_is_excluded():
    """Реальный случай: 427 из 576 текстовых страниц тома РД — единая
    подборка коммерческих предложений поставщика на 419 страниц. Её числа
    (масса, потери давления, цена) засоряют реестр помещений."""
    reason = non_project_reason("Потери давления, Па\nСтраница 5 из 419\nМасса, кг 1270")
    assert reason and "419" in reason, reason
    print("OK: страница каталога поставщика опознана как непроектный материал")


def test_commercial_offer_is_excluded():
    reason = non_project_reason('ООО "КОРФ"\nПредложение № KR22-064369/3\nГарантийный срок')
    assert reason, "коммерческое предложение должно отсеиваться"
    print("OK: коммерческое предложение опознано")


def test_normal_project_document_is_kept():
    """Нумерация «Страница 2 из 5» — обычный проектный документ, не каталог:
    порог знаменателя existence именно для этого."""
    assert non_project_reason("Общие данные\nСтраница 2 из 5") is None
    assert non_project_reason("АКТ ОСВИДЕТЕЛЬСТВОВАНИЯ СКРЫТЫХ РАБОТ № 51") is None
    assert non_project_reason("Экспликация помещений 1 этажа\n101 Тамбур 8.6") is None
    print("OK: проектные документы не отсеиваются")


if __name__ == "__main__":
    test_supplier_catalog_page_is_excluded()
    test_commercial_offer_is_excluded()
    test_normal_project_document_is_kept()
    print("ALL PASS")
