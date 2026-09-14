"""Тесты графа маршрутизации (Г.30 п.3).

Синтетика строится через `pymupdf` — `insert_text` для подписей и
`draw_line` для полилиний, чтобы геометрия была полностью
детерминированной. Плюс smoke-тесты на реальных листах комплекта:
они проверяют СТРУКТУРНЫЕ свойства (граф строится, состояние видимо,
ребро между поданными подписями находится), а не конкретные числа,
которые могли бы оказаться ответом слепого прогона (Г.24).
"""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routing_graph import (  # noqa: E402
    KIND_AXIS,
    KIND_BRANCH,
    KIND_ROOM,
    KIND_TARGET,
    Label,
    RoutingEdge,
    SegmentNetwork,
    build_routing_graph,
    collect_labels,
    diff_routing_graphs,
    page_segments,
    segment_distance,
)

SAMPLE_DIR = Path("/home/user/nadzor_sample")
OV1 = SAMPLE_DIR / "АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf"
OV1_PLAN_PAGE = 17
OV2 = SAMPLE_DIR / "АНО-150321-1-РД-ОВ2.1_изм. 3_в1.pdf"
OV2_PLAN_PAGE = 16
# Зона правого крыла плана 1 этажа, где стоят подписи В2.x и М.О. поз.169.
OV1_ZONE = (1980.0, 440.0, 2384.0, 980.0)
# Рамки подписей, СНЯТЫЕ С ЛИСТА вручную (подписи нарисованы кривыми, см.
# докстринг модуля). Это ВХОД теста — то, что на CAD-листе пришлось бы
# получить от эскалации на зрение, — а не ожидаемый ответ.
OV1_BRANCH_LABEL = Label("В2.7", (2093.1, 822.3, 2113.4, 833.8), KIND_BRANCH)
OV1_TARGET_LABEL = Label("М.О. поз.169", (2320.0, 814.2, 2348.3, 836.9), KIND_TARGET)
OV1_ROOM_KEYS = {"140", "141", "142", "147"}


# --------------------------------------------------------------------------
# синтетика
# --------------------------------------------------------------------------

def _new_page(width=600, height=400):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    page.insert_font(fontname="F0", fontbuffer=font.buffer)
    return doc, page


def _text(page, xy, value, size=10):
    page.insert_text(xy, value, fontname="F0", fontsize=size)


def _polyline(page, points):
    for a, b in zip(points, points[1:]):
        page.draw_line(a, b)


def _duct(page):
    """Магистраль из четырёх узлов: стояк слева, горизонтальный прогон,
    спуск к точке сбора справа."""
    _polyline(page, [(58, 90), (58, 200), (300, 200), (300, 320)])


def test_branch_reaches_target_through_duct():
    """Базовый случай: выноска ветки подведена к магистрали, выноска точки
    сбора — к той же магистрали. Ребро room → branch → target строится."""
    doc, page = _new_page()
    try:
        _duct(page)
        _text(page, (100, 100), "В2.7")
        _polyline(page, [(99, 97), (55, 97)])          # пересекает стояк x=58
        _text(page, (350, 300), "М.О. поз.169")
        _polyline(page, [(349, 297), (295, 297)])      # пересекает спуск x=300
        _text(page, (150, 220), "141")
        _text(page, (500, 50), "142")

        graph = build_routing_graph(page, room_keys={"141", "142"})
        assert len(graph.edges) == 1, graph.edges
        edge = graph.edges[0]
        assert edge.resolved, edge
        assert not edge.ambiguous, edge
        assert edge.branch_code == "В2.7", edge
        assert edge.target_code == "М.О. поз.169", edge
        assert edge.room_key == "141", edge
    finally:
        doc.close()
    print("OK: ветка, подведённая к магистрали, доходит до точки сбора")


def test_axis_label_used_as_anchor_when_no_room_present():
    """Обобщение на листы без экспликации помещений (Г.5): если меток
    KIND_ROOM нет вообще, ближайшая ось (`axes.py`) даёт ключ ровно тем же
    механизмом — geometry не различает источник метки, только kind."""
    doc, page = _new_page()
    try:
        _duct(page)
        _text(page, (100, 100), "В2.7")
        _polyline(page, [(99, 97), (55, 97)])
        _text(page, (350, 300), "М.О. поз.169")
        _polyline(page, [(349, 297), (295, 297)])

        labels = list(collect_labels(page)) + [Label("АБ", (150, 215, 165, 225), KIND_AXIS)]
        graph = build_routing_graph(page, labels=labels)
        assert len(graph.edges) == 1, graph.edges
        edge = graph.edges[0]
        assert edge.resolved, edge
        assert edge.room_key == "АБ", edge
    finally:
        doc.close()
    print("OK: при пустом реестре помещений якорем становится ось, тем же кодом")


def test_room_wins_over_axis_when_physically_closer():
    """Оба типа якоря участвуют в поиске ближайшего ОДНОВРЕМЕННО (не по
    очереди) — при прочих равных выигрывает физически более близкий, а не
    тот, что стоит раньше в anchor_kinds."""
    doc, page = _new_page()
    try:
        _duct(page)
        _text(page, (100, 100), "В2.7")
        _polyline(page, [(99, 97), (55, 97)])
        _text(page, (350, 300), "М.О. поз.169")
        _polyline(page, [(349, 297), (295, 297)])

        labels = list(collect_labels(page, room_keys={"141"})) + [
            Label("141", (150, 215, 165, 225), KIND_ROOM),
            Label("АБ", (500, 500, 515, 510), KIND_AXIS),  # намеренно далеко от цепи
        ]
        graph = build_routing_graph(page, labels=labels)
        assert graph.edges[0].room_key == "141", graph.edges
    finally:
        doc.close()
    print("OK: физически более близкий якорь побеждает независимо от типа")


def test_anchor_kinds_restricts_candidates():
    """Явно ограниченный `anchor_kinds` исключает ось из поиска даже когда
    она физически ближе всего — вызывающий код может сузить якорь до
    только помещений, если хочет прежнее поведение."""
    doc, page = _new_page()
    try:
        _duct(page)
        _text(page, (100, 100), "В2.7")
        _polyline(page, [(99, 97), (55, 97)])
        _text(page, (350, 300), "М.О. поз.169")
        _polyline(page, [(349, 297), (295, 297)])

        labels = list(collect_labels(page)) + [Label("АБ", (150, 215, 165, 225), KIND_AXIS)]
        graph = build_routing_graph(page, labels=labels, anchor_kinds=(KIND_ROOM,))
        assert graph.edges[0].room_key is None, graph.edges
    finally:
        doc.close()
    print("OK: anchor_kinds сужает поиск якоря до явно указанных типов")


def test_branch_without_leader_is_visible_not_silent():
    """Г.10: подпись ветки, ни к чему не подведённая линией, даёт ребро с
    resolved=False и причиной, а не исчезает из результата."""
    doc, page = _new_page()
    try:
        _duct(page)
        _text(page, (400, 60), "В3.1")  # далеко от любой линии
        graph = build_routing_graph(page)
        assert len(graph.edges) == 1, graph.edges
        edge = graph.edges[0]
        assert not edge.resolved and not edge.ambiguous, edge
        assert edge.target_code is None, edge
        assert edge.reason, edge
        assert graph.unresolved() == [edge]
    finally:
        doc.close()
    print("OK: неразрешённая ветка присутствует в графе с причиной, а не молчит")


def test_two_leaders_to_different_chains_is_ambiguous():
    """Развилка: от подписи идут две выноски к двум разным магистралям.
    Наугад не разрешается — ambiguous=True (Г.30 п.3)."""
    doc, page = _new_page()
    try:
        _polyline(page, [(58, 90), (58, 200), (200, 200), (200, 320)])
        _polyline(page, [(400, 90), (400, 200), (500, 200), (500, 320)])
        _text(page, (250, 100), "В2.7")
        _polyline(page, [(249, 97), (55, 97)])    # к первой магистрали
        _polyline(page, [(274, 97), (405, 97)])   # ко второй
        graph = build_routing_graph(page)
        assert len(graph.edges) == 1, graph.edges
        edge = graph.edges[0]
        assert edge.ambiguous and not edge.resolved, edge
        assert "цеп" in edge.reason, edge
        assert graph.ambiguous() == [edge]
    finally:
        doc.close()
    print("OK: две выноски в разные цепи дают ambiguous, а не выбор наугад")


def test_two_targets_on_one_chain_is_ambiguous():
    """Две точки сбора на одной цепи — общий коллектор или пересечение;
    ребро помечается ambiguous и несёт обоих кандидатов."""
    doc, page = _new_page()
    try:
        _duct(page)
        _text(page, (100, 100), "В2.7")
        _polyline(page, [(99, 97), (55, 97)])
        _text(page, (350, 300), "М.О. поз.169")
        _polyline(page, [(349, 297), (295, 297)])
        _text(page, (350, 240), "М.О. поз.160")
        _polyline(page, [(349, 238), (295, 238)])   # тоже пересекает спуск
        graph = build_routing_graph(page)
        assert len(graph.edges) == 1, graph.edges
        edge = graph.edges[0]
        assert edge.ambiguous and not edge.resolved, edge
        assert set(edge.target_candidates) == {"М.О. поз.169", "М.О. поз.160"}, edge
    finally:
        doc.close()
    print("OK: две точки сбора на одной цепи дают ambiguous с обоими кандидатами")


def test_equidistant_rooms_leave_room_key_empty():
    """Два номера помещений равноудалены от цепи — по близости не
    различимы, room_key пуст, оба в room_candidates (Г.10)."""
    doc, page = _new_page()
    try:
        _duct(page)
        _text(page, (100, 100), "В2.7")
        _polyline(page, [(99, 97), (55, 97)])
        _text(page, (150, 220), "141")
        _text(page, (150, 185), "142")  # зеркально по другую сторону прогона
        graph = build_routing_graph(page, room_keys={"141", "142"})
        edge = graph.edges[0]
        assert edge.room_key is None, edge
        assert set(edge.room_candidates) == {"141", "142"}, edge
    finally:
        doc.close()
    print("OK: равноудалённые номера помещений не выбираются наугад")


def test_axis_line_is_not_a_chain():
    """Одиночный длинный отрезок (осевая/строительная линия) вплотную к
    подписи маршрутом не считается — правило по наблюдению у М.О. поз.169,
    где такой отрезок оказался ближайшим кандидатом."""
    doc, page = _new_page()
    try:
        page.draw_line((58, 20), (58, 380))  # два узла, через весь лист
        _text(page, (100, 100), "В2.7")
        _polyline(page, [(99, 97), (55, 97)])
        graph = build_routing_graph(page)
        edge = graph.edges[0]
        assert not edge.resolved, edge
        assert edge.reason, edge
    finally:
        doc.close()
    print("OK: одиночная осевая линия не принимается за цепь маршрута")


def test_category_column_is_not_a_branch_code():
    """«В2»/«В3»/«В4» в экспликации — графа «Кат. пом.», а не код системы:
    на стр. 17 реального листа таких слов 19 штук. Регулярка ветки требует
    точку с цифрой, и это правило зафиксировано тестом."""
    doc, page = _new_page()
    try:
        _text(page, (100, 100), "В2")
        _text(page, (100, 130), "В3")
        _text(page, (100, 160), "В2.7")
        labels = collect_labels(page)
        branches = [lb.text for lb in labels if lb.kind == KIND_BRANCH]
        assert branches == ["В2.7"], branches
    finally:
        doc.close()
    print("OK: категория помещения из экспликации не принимается за код ветки")


def test_target_phrase_is_collected_once():
    """«М.О. поз.169» — две words одной строки; жадный разбор даёт ОДНУ
    метку, иначе ветка увидела бы на своей цепи две точки сбора."""
    doc, page = _new_page()
    try:
        _text(page, (100, 100), "М.О. поз.169")
        targets = [lb.text for lb in collect_labels(page) if lb.kind == KIND_TARGET]
        assert targets == ["М.О. поз.169"], targets
    finally:
        doc.close()
    print("OK: составная подпись точки сбора собирается в одну метку")


def test_notes_explain_empty_graph_on_a_drawing_page():
    """Лист с графикой, но без подписей веток в источнике: пустой граф
    объяснён в notes, а не выдан молча (Г.10)."""
    doc, page = _new_page()
    try:
        _duct(page)
        graph = build_routing_graph(page)
        assert graph.edges == (), graph.edges
        assert graph.notes, graph.notes
        assert any("подписей веток" in note for note in graph.notes), graph.notes
    finally:
        doc.close()
    print("OK: пустой граф на графическом листе объяснён, а не молчит")


def test_segment_distance_counts_crossing_as_zero():
    """Пересекающиеся отрезки — расстояние 0: именно так рамка символа
    местного отсоса сходится с воздуховодом, не имея с ним общих узлов."""
    assert segment_distance((0, 0), (10, 0), (5, -5), (5, 5)) == 0.0
    assert abs(segment_distance((0, 0), (10, 0), (0, 3), (10, 3)) - 3.0) < 1e-9
    print("OK: пересечение отрезков считается нулевым расстоянием")


# --------------------------------------------------------------------------
# сравнение сторон по рёбрам
# --------------------------------------------------------------------------

def _edge(room, branch, target):
    return RoutingEdge(branch_code=branch, room_key=room, target_code=target,
                       resolved=True)


def test_diff_renumbering_is_not_a_finding():
    """Г.30 п.5: та же точка сбора и то же число присоединений при других
    кодах веток — перенумерация, а не нарушение."""
    before = [_edge("141", "В2.7", "М.О. поз.169")]
    after = [_edge("141", "В5.3", "М.О. поз.169")]
    diff = diff_routing_graphs(before, after)
    assert len(diff["renumbered"]) == 1, diff
    assert not diff["retargeted"] and not diff["connection_count_changed"], diff
    print("OK: перенумерация веток не выдаётся за нарушение")


def test_diff_other_collection_point_is_a_finding():
    before = [_edge("141", "В2.7", "М.О. поз.169")]
    after = [_edge("141", "В2.7", "М.О. поз.160")]
    diff = diff_routing_graphs(before, after)
    assert len(diff["retargeted"]) == 1, diff
    print("OK: смена точки сбора у того же помещения — находка")


def test_diff_connection_count_change_is_a_finding():
    before = [_edge("141", "В2.7", "М.О. поз.169"),
              _edge("141", "В2.8", "М.О. поз.169")]
    after = [_edge("141", "В2.7", "М.О. поз.169")]
    diff = diff_routing_graphs(before, after)
    assert len(diff["connection_count_changed"]) == 1, diff
    print("OK: изменение числа присоединений — находка")


def test_diff_unresolved_edge_blocks_conclusion():
    """Г.10: неразрешённое ребро не даёт права сказать «расхождений нет»."""
    before = [_edge("141", "В2.7", "М.О. поз.169")]
    after = [RoutingEdge(branch_code="В2.7", room_key="141", resolved=False,
                         reason="цепь не найдена")]
    diff = diff_routing_graphs(before, after)
    assert len(diff["unusable"]) == 1, diff
    assert not diff["unchanged"] and not diff["retargeted"], diff
    print("OK: неразрешённое ребро уводит помещение в unusable, а не в «совпало»")


# --------------------------------------------------------------------------
# smoke на реальных листах
# --------------------------------------------------------------------------

def test_real_sheet_text_layer_carries_no_branch_labels():
    """Наблюдение с живого листа, зафиксированное тестом: на CAD-плане
    номера помещений в текстовом слое ЕСТЬ, а подписей веток и точек сбора
    НЕТ — они в кривых. Модуль обязан сказать это в notes, а не вернуть
    молчаливый пустой результат."""
    if not OV1.exists():
        print("SKIP: нет файла", OV1)
        return
    doc = pymupdf.open(OV1)
    try:
        page = doc[OV1_PLAN_PAGE]
        labels = collect_labels(page, room_keys=OV1_ROOM_KEYS, clip=OV1_ZONE)
        kinds = {lb.kind for lb in labels}
        assert KIND_ROOM in kinds, labels
        assert KIND_BRANCH not in kinds, labels
        graph = build_routing_graph(page, room_keys=OV1_ROOM_KEYS, clip=OV1_ZONE)
        assert graph.segment_count > 0, graph
        assert graph.edges == (), graph.edges
        assert any("подписей веток" in note for note in graph.notes), graph.notes
    finally:
        doc.close()
    print("OK: на реальном CAD-плане отсутствие подписей веток — видимое состояние")


def test_real_sheet_geometry_links_supplied_labels():
    """Геометрия на живом листе: с рамками подписей, снятыми вручную,
    ветка прослеживается до точки сбора. Проверяются структурные свойства
    (ребро разрешено, ведёт к поданной точке сбора, привязано к одному из
    поданных номеров помещений), а не конкретные числа (Г.24).

    Номера помещений подаются ВСЕ, включая их вторые вхождения в
    экспликации внизу листа — привязка по близости обязана справиться с
    этим шумом без отдельного детектора таблицы."""
    if not OV1.exists():
        print("SKIP: нет файла", OV1)
        return
    doc = pymupdf.open(OV1)
    try:
        page = doc[OV1_PLAN_PAGE]
        rooms = [Label(w[4], tuple(w[:4]), KIND_ROOM) for w in page.get_text("words")
                 if w[4] in OV1_ROOM_KEYS]
        assert rooms, "номера помещений на плане должны читаться текстом"
        assert len(rooms) > len({lb.text for lb in rooms}), \
            "ожидались повторные вхождения номеров (план + экспликация)"
        graph = build_routing_graph(
            page, labels=[OV1_BRANCH_LABEL, OV1_TARGET_LABEL, *rooms], clip=OV1_ZONE)
        assert len(graph.edges) == 1, graph.edges
        edge = graph.edges[0]
        assert edge.resolved and not edge.ambiguous, (edge, graph.notes)
        assert edge.branch_code == OV1_BRANCH_LABEL.text, edge
        assert edge.target_code == OV1_TARGET_LABEL.text, edge
        assert edge.room_key in OV1_ROOM_KEYS, edge
        assert edge.chain_size > 0, edge
    finally:
        doc.close()
    print("OK: на реальном листе ветка прослеживается до точки сбора по геометрии")


def test_real_sheet_second_volume_builds_without_crashing():
    """Второй том (план 2 этажа, отопление): граф строится, состояние
    видимо. Никаких утверждений о содержании — только что механика не
    падает на другом листе того же комплекта."""
    if not OV2.exists():
        print("SKIP: нет файла", OV2)
        return
    doc = pymupdf.open(OV2)
    try:
        page = doc[OV2_PLAN_PAGE]
        graph = build_routing_graph(page, clip=OV1_ZONE)
        assert graph.segment_count > 0, graph
        assert graph.component_count > 0, graph
        assert graph.notes, graph.notes
    finally:
        doc.close()
    print("OK: на листе второго тома граф строится без падения")


def test_real_sheet_network_does_not_collapse_into_one_blob():
    """Замер, на котором стоит выбор малого snap: цепь воздуховода должна
    оставаться отдельной компонентой, а не сливаться со всем листом.
    Проверяется структурно — компонент много, крупнейшая не покрывает
    большую часть узлов."""
    if not OV1.exists():
        print("SKIP: нет файла", OV1)
        return
    doc = pymupdf.open(OV1)
    try:
        page = doc[OV1_PLAN_PAGE]
        network = SegmentNetwork(page_segments(page, OV1_ZONE))
        assert network.component_count > 100, network.component_count
        sizes = [network.component_size(root) for root in network._members]
        assert max(sizes) < sum(sizes) / 2, (max(sizes), sum(sizes))
    finally:
        doc.close()
    print("OK: сеть связности реального листа не схлопывается в одну компоненту")


if __name__ == "__main__":
    test_branch_reaches_target_through_duct()
    test_axis_label_used_as_anchor_when_no_room_present()
    test_room_wins_over_axis_when_physically_closer()
    test_anchor_kinds_restricts_candidates()
    test_branch_without_leader_is_visible_not_silent()
    test_two_leaders_to_different_chains_is_ambiguous()
    test_two_targets_on_one_chain_is_ambiguous()
    test_equidistant_rooms_leave_room_key_empty()
    test_axis_line_is_not_a_chain()
    test_category_column_is_not_a_branch_code()
    test_target_phrase_is_collected_once()
    test_notes_explain_empty_graph_on_a_drawing_page()
    test_segment_distance_counts_crossing_as_zero()
    test_diff_renumbering_is_not_a_finding()
    test_diff_other_collection_point_is_a_finding()
    test_diff_connection_count_change_is_a_finding()
    test_diff_unresolved_edge_blocks_conclusion()
    test_real_sheet_text_layer_carries_no_branch_labels()
    test_real_sheet_geometry_links_supplied_labels()
    test_real_sheet_second_volume_builds_without_crashing()
    test_real_sheet_network_does_not_collapse_into_one_blob()
    print("ALL PASS")
