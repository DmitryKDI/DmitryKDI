"""Blind high-recall comparison regions for drawings without reliable ROOM anchors.

The module deliberately separates navigation from semantic evidence.  Room,
axis, equipment/system, geometry and model-proposed regions are only ways to
decide where to look.  PASS 1 discovers candidate engineering differences and
PASS 2 verifies them.  Weak anchors, raster similarity and geometry fingerprints
must never become semantic findings or hard "same" gates.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Sequence

import pymupdf

from .comparison_anchors import anchor_kind, anchor_search_terms, anchor_value
from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_png_bytes


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_GENERIC_REGION_CALLS = _int_env("NADZOR_MAX_GENERIC_REGION_VISION_CALLS", 18, 0, 64)
MAX_GENERIC_CALLS_PER_PAIR = _int_env("NADZOR_MAX_GENERIC_CALLS_PER_PAIR", 4, 1, 10)
MAX_GEOMETRY_REGIONS_PER_PAIR = _int_env("NADZOR_MAX_GEOMETRY_REGIONS_PER_PAIR", 3, 1, 8)
MAX_PASS0_REGIONS = _int_env("NADZOR_MAX_PASS0_REGIONS_PER_PAIR", 4, 1, 8)
GENERAL_MAX_DIM = _int_env("NADZOR_GENERIC_GENERAL_MAX_DIM", 2400, 1200, 2800)
LOCAL_MAX_DIM = _int_env("NADZOR_GENERIC_LOCAL_MAX_DIM", 2600, 1400, 2800)

_PASS0 = f"""Ты — навигационный vision-pass для инженерных чертежей ПД и РД/ИД.
У тебя только две общие картинки листов. Не ищи нарушения и не делай findings.
Предложи до {MAX_PASS0_REGIONS} пар локальных областей, которые разумно
сравнить подробнее, когда надёжных ROOM/AXIS/SYSTEM anchors недостаточно.

Ищи инженерно насыщенные и сопоставимые области: узлы сетей, оборудование,
ветвления, терминалы, повторяющиеся схемные модули. Архитектурный фон, штампы,
рамки, мебель и обычный текст не являются целью. Если типы листов различаются
(например схема и план), допускаются разные координаты PD/RD, если область
похожа по инженерной роли.

bbox = [x0,y0,x1,y1] в долях 0..1 от верхнего левого угла соответствующего
листа. Области не должны полностью дублировать друг друга.

{UNTRUSTED_INPUT_RULE}

Ответ только JSON:
{{"regions":[{{"id":"r1","type":"AXIS_CELL|EQUIPMENT|GEOMETRY|GENERIC",
"pd_bbox":[0.0,0.0,1.0,1.0],"rd_bbox":[0.0,0.0,1.0,1.0],
"reason":"navigation only"}}]}}"""

_PASS1 = f"""Ты — vision-аналитик инженерных чертежей. Слепо сравни одну
universal comparison_region ПД и РД/ИД. Регион может быть ROOM, AXIS,
EQUIPMENT/SYSTEM, GEOMETRY или ADAPTIVE. room_id не обязателен.

Вход — 4 отдельных изображения: общий ПД, локальный ПД, общий РД/ИД,
локальный РД/ИД. Anchor metadata, pair confidence и provenance используются
ТОЛЬКО для навигации и не являются доказательством различия.

Сначала независимо опиши видимые инженерные сущности и связи на ПД и РД.
Проверь inventory, topology, connections, parameters и boundary connections.
Если типы графики различаются, сопоставляй инженерные сущности и граф связей,
а не абсолютные координаты. При наблюдаемом основании сохрани candidate
difference. Не удаляй кандидат из-за неуверенности: вынеси сомнение в
uncertainty_reasons.

comparability=high только если локальные области действительно сопоставимы и
видимость достаточна. medium/low — при слабой привязке, разном масштабе/типе
графики, частичном crop, неоднозначных символах или плохой читаемости.
PASS 1 не имеет финального semantic "same".

{UNTRUSTED_INPUT_RULE}

Ответ только JSON:
{{"region_id":"string","execution_state":"completed",
"visibility_pd":"seen|partial|poor","visibility_rd":"seen|partial|poor",
"comparability":"high|medium|low",
"engineering_elements_pd":[],"engineering_elements_rd":[],
"connections_pd":[],"connections_rd":[],
"topology_observations":[],
"candidate_differences":[{{"category":"inventory|topology|connection|parameter|drawing_type_mismatch",
"description":"конкретный наблюдаемый факт","location_hint":"string|null"}}],
"uncertainty_reasons":[],
"coverage":["inventory","topology","connections","parameters","boundary_connections"],
"status":"changed_candidate|unchanged_candidate|unclear","summary":"кратко"}}"""

_VERIFY = f"""Ты — второй vision-pass. Проверь candidate_differences для одной
universal comparison_region по тем же 4 изображениям.

Anchor metadata — только навигация. Для каждого кандидата verdict:
confirmed|rejected|unclear. rejected разрешён ТОЛЬКО если и PASS1, и текущий
pass имеют comparability=high И есть явное визуальное доказательство, что
кандидат ошибочен. При medium/low comparability, частичном crop, неоднозначном
символе или несовпадении типов представления возвращай unclear, не rejected.

{UNTRUSTED_INPUT_RULE}

Ответ только JSON:
{{"comparability":"high|medium|low","verified":[{{"idx":0,
"verdict":"confirmed|rejected|unclear","reason":"кратко",
"location":"string|null"}}],"page_status":"changed|unchanged|unclear",
"status_notes":[]}}"""


@dataclass(frozen=True)
class RegionProposal:
    region_id: str
    anchor_type: str
    anchor_ids: tuple[str, ...]
    pd_clip: tuple[float, float, float, float]
    rd_clip: tuple[float, float, float, float]
    pair_confidence: float
    provenance: str


def _clip_fraction(page: pymupdf.Page, rect: pymupdf.Rect, pad: float = 0.16):
    pr = page.rect
    w, h = max(1.0, rect.width), max(1.0, rect.height)
    rect = pymupdf.Rect(
        max(pr.x0, rect.x0 - max(pr.width * pad, w * 2.0)),
        max(pr.y0, rect.y0 - max(pr.height * pad, h * 2.0)),
        min(pr.x1, rect.x1 + max(pr.width * pad, w * 2.0)),
        min(pr.y1, rect.y1 + max(pr.height * pad, h * 2.0)),
    )
    return (
        max(0.0, min(1.0, rect.x0 / max(1.0, pr.width))),
        max(0.0, min(1.0, rect.y0 / max(1.0, pr.height))),
        max(0.0, min(1.0, rect.x1 / max(1.0, pr.width))),
        max(0.0, min(1.0, rect.y1 / max(1.0, pr.height))),
    )


def _normalize_clip(value) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 < 0.05 or y1 - y0 < 0.05:
        return None
    return (x0, y0, x1, y1)


def _rect_iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    return inter / max(1e-9, area_a + area_b - inter)


def _anchor_region(path: str, page_no: int, key: str):
    """Localize a system/equipment label and pull nearby connector geometry in."""
    try:
        doc = pymupdf.open(path)
        try:
            page = doc[page_no - 1]
            pr = page.rect
            hits = []
            for term in anchor_search_terms(key):
                hits.extend(page.search_for(term))
            if not hits:
                return None

            def stamp_penalty(rect):
                cx = (rect.x0 + rect.x1) / 2
                cy = (rect.y0 + rect.y1) / 2
                return (
                    int(cy > pr.height * 0.86) + int(cx > pr.width * 0.90),
                    cy,
                    cx,
                )

            hits.sort(key=stamp_penalty)
            label = pymupdf.Rect(hits[0])
            context = pymupdf.Rect(
                max(pr.x0, label.x0 - pr.width * 0.07),
                max(pr.y0, label.y0 - pr.height * 0.07),
                min(pr.x1, label.x1 + pr.width * 0.07),
                min(pr.y1, label.y1 + pr.height * 0.07),
            )
            merged = pymupdf.Rect(label)
            page_area = max(1.0, pr.width * pr.height)
            attached = 0
            for drawing in page.get_drawings():
                raw = drawing.get("rect")
                if not raw:
                    continue
                rect = pymupdf.Rect(raw)
                if rect.width * rect.height > page_area * 0.12:
                    continue
                if rect.intersects(context):
                    merged |= rect
                    attached += 1
                    if attached >= 80:
                        break
            return _clip_fraction(page, merged, pad=0.08)
        finally:
            doc.close()
    except Exception:
        return None


def _nearest_edge(rect: pymupdf.Rect, pr: pymupdf.Rect) -> tuple[str, float]:
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    distances = {
        "left": abs(cx - pr.x0),
        "right": abs(pr.x1 - cx),
        "top": abs(cy - pr.y0),
        "bottom": abs(pr.y1 - cy),
    }
    edge = min(distances, key=distances.get)
    return edge, distances[edge]


def _adjacent_pair(values: Sequence[tuple[float, str]], target: float):
    values = sorted(values)
    if len(values) < 2:
        return None
    return min(
        ((values[i], values[i + 1]) for i in range(len(values) - 1)),
        key=lambda pair: abs(((pair[0][0] + pair[1][0]) / 2) - target),
    )


def _axis_region(path: str, page_no: int, keys: Sequence[str]):
    """Build a cell-like ROI from axis labels instead of cropping the labels."""
    axis_keys = [key for key in keys if anchor_kind(key) == "axis"]
    if len(axis_keys) < 2:
        return None
    try:
        doc = pymupdf.open(path)
        try:
            page = doc[page_no - 1]
            pr = page.rect
            x_axes: list[tuple[float, str]] = []
            y_axes: list[tuple[float, str]] = []

            for key in axis_keys[:12]:
                found = []
                for term in anchor_search_terms(key):
                    found.extend(page.search_for(term))
                if not found:
                    continue
                found.sort(key=lambda r: _nearest_edge(r, pr)[1])
                hit = pymupdf.Rect(found[0])
                edge, distance = _nearest_edge(hit, pr)
                if distance > max(pr.width, pr.height) * 0.20:
                    continue
                cx = (hit.x0 + hit.x1) / 2
                cy = (hit.y0 + hit.y1) / 2
                if edge in {"top", "bottom"}:
                    x_axes.append((cx, key))
                else:
                    y_axes.append((cy, key))

            used: list[str] = []
            x_pair = _adjacent_pair(x_axes, pr.width / 2)
            y_pair = _adjacent_pair(y_axes, pr.height / 2)

            if x_pair and y_pair:
                (x0, kx0), (x1, kx1) = x_pair
                (y0, ky0), (y1, ky1) = y_pair
                rect = pymupdf.Rect(x0, y0, x1, y1)
                used.extend((kx0, kx1, ky0, ky1))
            elif x_pair and y_axes:
                (x0, kx0), (x1, kx1) = x_pair
                y, ky = min(y_axes, key=lambda row: abs(row[0] - pr.height / 2))
                rect = pymupdf.Rect(
                    x0,
                    max(pr.y0, y - pr.height * 0.18),
                    x1,
                    min(pr.y1, y + pr.height * 0.18),
                )
                used.extend((kx0, kx1, ky))
            elif y_pair and x_axes:
                (y0, ky0), (y1, ky1) = y_pair
                x, kx = min(x_axes, key=lambda row: abs(row[0] - pr.width / 2))
                rect = pymupdf.Rect(
                    max(pr.x0, x - pr.width * 0.18),
                    y0,
                    min(pr.x1, x + pr.width * 0.18),
                    y1,
                )
                used.extend((ky0, ky1, kx))
            elif x_pair:
                (x0, kx0), (x1, kx1) = x_pair
                rect = pymupdf.Rect(x0, pr.height * 0.30, x1, pr.height * 0.70)
                used.extend((kx0, kx1))
            elif y_pair:
                (y0, ky0), (y1, ky1) = y_pair
                rect = pymupdf.Rect(pr.width * 0.30, y0, pr.width * 0.70, y1)
                used.extend((ky0, ky1))
            elif x_axes and y_axes:
                x, kx = min(x_axes, key=lambda row: abs(row[0] - pr.width / 2))
                y, ky = min(y_axes, key=lambda row: abs(row[0] - pr.height / 2))
                rect = pymupdf.Rect(
                    max(pr.x0, x - pr.width * 0.16),
                    max(pr.y0, y - pr.height * 0.16),
                    min(pr.x1, x + pr.width * 0.16),
                    min(pr.y1, y + pr.height * 0.16),
                )
                used.extend((kx, ky))
            else:
                return None

            rect &= pr
            unique_used = tuple(dict.fromkeys(used))
            if len(unique_used) < 2:
                return None
            return _clip_fraction(page, rect, pad=0.06), unique_used
        finally:
            doc.close()
    except Exception:
        return None


def _rect_gap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0.0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0.0)
    return math.hypot(dx, dy)


def _line_orientation_bins(drawing: dict) -> tuple[int, int, int, int]:
    bins = [0, 0, 0, 0]
    for item in drawing.get("items") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3 or item[0] != "l":
            continue
        p0, p1 = item[1], item[2]
        try:
            dx = float(p1.x) - float(p0.x)
            dy = float(p1.y) - float(p0.y)
        except (AttributeError, TypeError, ValueError):
            continue
        if abs(dx) + abs(dy) < 1e-6:
            continue
        angle = math.degrees(math.atan2(abs(dy), abs(dx)))
        if angle < 22.5:
            bins[0] += 1
        elif angle > 67.5:
            bins[1] += 1
        elif dx * dy >= 0:
            bins[2] += 1
        else:
            bins[3] += 1
    return tuple(bins)


def _geometry_clusters(path: str, page_no: int, limit: int = 6):
    """Return vector clusters with richer, scale-tolerant navigation descriptors."""
    try:
        doc = pymupdf.open(path)
        try:
            page = doc[page_no - 1]
            pr = page.rect
            page_area = max(1.0, pr.width * pr.height)
            entries = []
            for drawing in page.get_drawings():
                raw = drawing.get("rect")
                if not raw:
                    continue
                rect = pymupdf.Rect(raw)
                area = max(0.0, rect.width * rect.height)
                if area <= page_area * 0.000002 or area >= page_area * 0.35:
                    continue
                style = (
                    round(float(drawing.get("width") or 0.0), 2),
                    str(drawing.get("dashes") or ""),
                    str(drawing.get("color") or ""),
                )
                entries.append((rect, _line_orientation_bins(drawing), style))
            entries = entries[:320]
            if len(entries) < 3:
                return []

            proximity = max(pr.width, pr.height) * 0.012
            parent = list(range(len(entries)))
            degree = [0] * len(entries)

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            for i, (a, _, _) in enumerate(entries):
                for j in range(i + 1, min(len(entries), i + 64)):
                    if _rect_gap(a, entries[j][0]) <= proximity:
                        union(i, j)
                        degree[i] += 1
                        degree[j] += 1

            groups: dict[int, list[int]] = {}
            for i in range(len(entries)):
                groups.setdefault(find(i), []).append(i)

            words = page.get_text("words") or []
            out = []
            for indices in groups.values():
                if len(indices) < 3:
                    continue
                group_rects = [entries[i][0] for i in indices]
                bbox = pymupdf.Rect(group_rects[0])
                for rect in group_rects[1:]:
                    bbox |= rect
                frac_area = bbox.width * bbox.height / page_area
                if not 0.001 <= frac_area <= 0.32:
                    continue

                bbox_area = max(1.0, bbox.width * bbox.height)
                primitive_area = sum(
                    max(1.0, min(r.width * r.height, bbox_area * 0.25))
                    for r in group_rects
                )
                density = min(1.0, primitive_area / bbox_area)
                aspect = bbox.width / max(1.0, bbox.height)
                log_aspect = min(1.0, abs(math.log(max(1e-4, aspect))) / 2.5)

                orientation = [0, 0, 0, 0]
                styles = set()
                for i in indices:
                    for k, value in enumerate(entries[i][1]):
                        orientation[k] += value
                    styles.add(entries[i][2])
                ori_total = max(1, sum(orientation))
                ori_frac = [value / ori_total for value in orientation]

                near_boundary = 0
                small = 0
                margin_x = max(1.0, bbox.width * 0.06)
                margin_y = max(1.0, bbox.height * 0.06)
                for rect in group_rects:
                    cx = (rect.x0 + rect.x1) / 2
                    cy = (rect.y0 + rect.y1) / 2
                    if (
                        abs(cx - bbox.x0) <= margin_x
                        or abs(cx - bbox.x1) <= margin_x
                        or abs(cy - bbox.y0) <= margin_y
                        or abs(cy - bbox.y1) <= margin_y
                    ):
                        near_boundary += 1
                    if rect.width * rect.height <= bbox_area * 0.01:
                        small += 1

                group_degrees = [degree[i] for i in indices]
                degree_low = sum(1 for d in group_degrees if d <= 1) / len(indices)
                degree_mid = sum(1 for d in group_degrees if 2 <= d <= 4) / len(indices)
                degree_high = sum(1 for d in group_degrees if d >= 5) / len(indices)

                word_count = 0
                for word in words:
                    if len(word) < 4:
                        continue
                    try:
                        wr = pymupdf.Rect(word[:4])
                    except Exception:
                        continue
                    if wr.intersects(bbox):
                        word_count += 1

                fingerprint = (
                    min(1.0, len(indices) / 80.0),
                    log_aspect,
                    density,
                    min(1.0, frac_area / 0.32),
                    *ori_frac,
                    near_boundary / len(indices),
                    min(1.0, len(styles) / 12.0),
                    min(1.0, word_count / 30.0),
                    small / len(indices),
                    degree_low,
                    degree_mid,
                    degree_high,
                )
                structural_score = (
                    len(indices)
                    * (0.45 + density)
                    * (1.0 + min(1.0, sum(orientation) / 20.0) * 0.25)
                )
                clip = _clip_fraction(page, bbox, pad=0.07)
                out.append((structural_score, fingerprint, clip))

            out.sort(key=lambda row: -row[0])
            return out[:limit]
        finally:
            doc.close()
    except Exception:
        return []


def _fp_distance(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def propose_regions(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    shared_anchors: Sequence[str],
):
    proposals: list[RegionProposal] = []

    pd_axis = _axis_region(before_path, before_page, shared_anchors)
    rd_axis = _axis_region(after_path, after_page, shared_anchors)
    if pd_axis and rd_axis:
        pd_clip, pd_used = pd_axis
        rd_clip, rd_used = rd_axis
        rd_keys = set(rd_used)
        used = tuple(key for key in pd_used if key in rd_keys)
        if len(used) >= 2:
            proposals.append(
                RegionProposal(
                    region_id="axis_zone:" + "+".join(anchor_value(key) for key in used[:4]),
                    anchor_type="axis",
                    anchor_ids=used[:4],
                    pd_clip=pd_clip,
                    rd_clip=rd_clip,
                    pair_confidence=0.78,
                    provenance="deterministic_axis_cell",
                )
            )

    for key in shared_anchors:
        if anchor_kind(key) != "equipment_system":
            continue
        pd_clip = _anchor_region(before_path, before_page, key)
        rd_clip = _anchor_region(after_path, after_page, key)
        if pd_clip and rd_clip:
            proposals.append(
                RegionProposal(
                    region_id=f"equipment_system:{anchor_value(key)}",
                    anchor_type="equipment_system",
                    anchor_ids=(key,),
                    pd_clip=pd_clip,
                    rd_clip=rd_clip,
                    pair_confidence=0.76,
                    provenance="deterministic_text_anchor_with_connectors",
                )
            )
        if sum(1 for p in proposals if p.anchor_type == "equipment_system") >= 2:
            break

    if len(proposals) < MAX_GEOMETRY_REGIONS_PER_PAIR:
        pd_clusters = _geometry_clusters(before_path, before_page)
        rd_clusters = _geometry_clusters(after_path, after_page)
        used_rd = set()
        matched = []
        for pi, (_, pfp, pclip) in enumerate(pd_clusters):
            choices = [
                (_fp_distance(pfp, rfp), ri, rclip)
                for ri, (_, rfp, rclip) in enumerate(rd_clusters)
                if ri not in used_rd
            ]
            if not choices:
                continue
            distance, ri, rclip = min(choices)
            if distance > 0.32:
                continue
            used_rd.add(ri)
            matched.append((distance, pi, pclip, ri, rclip))
        matched.sort()
        for distance, pi, pclip, ri, rclip in matched:
            if len(proposals) >= MAX_GEOMETRY_REGIONS_PER_PAIR:
                break
            proposals.append(
                RegionProposal(
                    region_id=f"geometry:{pi}-{ri}",
                    anchor_type="geometry",
                    anchor_ids=(),
                    pd_clip=pclip,
                    rd_clip=rclip,
                    pair_confidence=max(0.38, 0.72 - distance),
                    provenance="deterministic_geometry_descriptor",
                )
            )
    return proposals


def _pass0_regions(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    config: LlmConfig,
    existing: Sequence[RegionProposal],
    discipline: str,
):
    pd = render_page_to_png_bytes(before_path, before_page, max_dim=GENERAL_MAX_DIM)
    rd = render_page_to_png_bytes(after_path, after_page, max_dim=GENERAL_MAX_DIM)
    images = [png_bytes_to_data_url(pd), png_bytes_to_data_url(rd)]
    text = (
        f"discipline={discipline or 'unknown'}. "
        "Это только navigation discovery; никаких findings."
    )
    digest = hashlib.sha256(
        f"{before_path}:{before_page}:{after_path}:{after_page}:pass0".encode()
    ).hexdigest()
    raw = call_llm_json(
        config,
        _PASS0,
        text,
        images=images,
        operation="vision",
        source_digest=digest,
        prompt_version="generic-region-pass0-v1",
    )
    raw = raw if isinstance(raw, dict) else {}
    proposals = []
    for idx, item in enumerate(raw.get("regions") or []):
        if not isinstance(item, dict):
            continue
        pd_clip = _normalize_clip(item.get("pd_bbox"))
        rd_clip = _normalize_clip(item.get("rd_bbox"))
        if not pd_clip or not rd_clip:
            continue
        if any(
            _rect_iou(pd_clip, p.pd_clip) > 0.72 and _rect_iou(rd_clip, p.rd_clip) > 0.72
            for p in existing
        ):
            continue
        raw_type = str(item.get("type") or "GENERIC").casefold()
        if raw_type not in {"axis_cell", "equipment", "geometry", "generic"}:
            raw_type = "generic"
        proposals.append(
            RegionProposal(
                region_id=f"pass0:{idx + 1}",
                anchor_type=f"adaptive_{raw_type}",
                anchor_ids=(),
                pd_clip=pd_clip,
                rd_clip=rd_clip,
                pair_confidence=0.46,
                provenance="model_pass0_navigation_only",
            )
        )
        if len(proposals) >= MAX_PASS0_REGIONS:
            break
    return proposals


def _view(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    region: RegionProposal,
):
    return {
        "region": region,
        "pd_general": render_page_to_png_bytes(
            before_path, before_page, max_dim=GENERAL_MAX_DIM
        ),
        "pd_local": render_page_to_png_bytes(
            before_path, before_page, max_dim=LOCAL_MAX_DIM, clip_frac=region.pd_clip
        ),
        "rd_general": render_page_to_png_bytes(
            after_path, after_page, max_dim=GENERAL_MAX_DIM
        ),
        "rd_local": render_page_to_png_bytes(
            after_path, after_page, max_dim=LOCAL_MAX_DIM, clip_frac=region.rd_clip
        ),
    }


def _images(view):
    return [
        png_bytes_to_data_url(view[key])
        for key in ("pd_general", "pd_local", "rd_general", "rd_local")
    ]


def _call_pass1(view, config: LlmConfig, discipline: str):
    region: RegionProposal = view["region"]
    text = (
        f"region_id={region.region_id}; anchor_type={region.anchor_type}; "
        f"anchor_ids={list(region.anchor_ids)}; "
        f"pair_confidence={region.pair_confidence:.3f}; "
        f"provenance={region.provenance}; discipline={discipline or 'unknown'}. "
        "Anchor metadata — navigation only."
    )
    digest = hashlib.sha256((region.region_id + text).encode()).hexdigest()
    out = call_llm_json(
        config,
        _PASS1,
        text,
        images=_images(view),
        operation="vision",
        source_digest=digest,
        prompt_version="generic-region-pass1-v2",
    )
    return out if isinstance(out, dict) else {}


def _normalize_pass1(raw: dict, region: RegionProposal):
    comparability = str(raw.get("comparability") or "low").casefold()
    if comparability not in {"high", "medium", "low"}:
        comparability = "low"
    candidates = []
    for item in raw.get("candidate_differences") or []:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        candidates.append(
            {
                "category": str(item.get("category") or "topology"),
                "description": description,
                "location_hint": str(item.get("location_hint") or "").strip() or None,
            }
        )
    status = (
        "changed_candidate"
        if candidates
        else str(raw.get("status") or "unclear").casefold()
    )
    if status not in {"changed_candidate", "unchanged_candidate", "unclear"}:
        status = "unclear"
    return {
        "region_id": region.region_id,
        "anchor_type": region.anchor_type,
        "anchor_ids": list(region.anchor_ids),
        "anchor_provenance": region.provenance,
        "pair_confidence": region.pair_confidence,
        "execution_state": str(raw.get("execution_state") or "completed"),
        "comparability": comparability,
        "visibility_pd": str(raw.get("visibility_pd") or "partial"),
        "visibility_rd": str(raw.get("visibility_rd") or "partial"),
        "engineering_elements_pd": raw.get("engineering_elements_pd") or [],
        "engineering_elements_rd": raw.get("engineering_elements_rd") or [],
        "connections_pd": raw.get("connections_pd") or [],
        "connections_rd": raw.get("connections_rd") or [],
        "topology_observations": raw.get("topology_observations") or [],
        "candidate_differences": candidates,
        "uncertainty_reasons": raw.get("uncertainty_reasons") or [],
        "coverage": raw.get("coverage") or [],
        "status": status,
        "summary": str(raw.get("summary") or ""),
    }


def _verify(view, pass1: dict, config: LlmConfig, discipline: str):
    candidates = pass1["candidate_differences"]
    if not candidates:
        return []
    region: RegionProposal = view["region"]
    text = (
        f"region_id={region.region_id}; PASS1 comparability={pass1['comparability']}; "
        f"discipline={discipline or 'unknown'}; "
        f"candidates={json.dumps(candidates, ensure_ascii=False)}"
    )
    digest = hashlib.sha256((region.region_id + text).encode()).hexdigest()
    raw = call_llm_json(
        config,
        _VERIFY,
        text,
        images=_images(view),
        operation="vision",
        source_digest=digest,
        prompt_version="generic-region-verify-v2",
    )
    raw = raw if isinstance(raw, dict) else {}
    verifier_comp = str(
        raw.get("comparability") or pass1["comparability"]
    ).casefold()
    if verifier_comp not in {"high", "medium", "low"}:
        verifier_comp = pass1["comparability"]

    found = {}
    for item in raw.get("verified") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(candidates):
            continue
        verdict = str(item.get("verdict") or "unclear").casefold()
        if verdict not in {"confirmed", "rejected", "unclear"}:
            verdict = "unclear"
        if verdict == "rejected" and (
            pass1["comparability"] != "high" or verifier_comp != "high"
        ):
            verdict = "unclear"
        found[idx] = {
            "idx": idx,
            "verdict": verdict,
            "reason": str(item.get("reason") or ""),
            "location": item.get("location"),
            "comparability": verifier_comp,
        }

    return [
        found.get(
            i,
            {
                "idx": i,
                "verdict": "unclear",
                "reason": "verification omitted candidate",
                "location": None,
                "comparability": verifier_comp,
            },
        )
        for i in range(len(candidates))
    ]


def _proposal_priority(region: RegionProposal):
    kind = region.anchor_type
    if kind == "axis":
        tier = 4
    elif kind == "equipment_system":
        tier = 3
    elif kind == "geometry":
        tier = 2
    else:
        tier = 1
    return (-tier, -region.pair_confidence, region.region_id)


def compare_non_room_regions(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    shared_anchors: Sequence[str],
    config: LlmConfig,
    *,
    max_calls: int,
    discipline: str = "",
):
    if max_calls <= 0:
        return [], [], 0

    budget = min(max_calls, MAX_GENERIC_CALLS_PER_PAIR)
    used = 0
    proposals = propose_regions(
        before_path, before_page, after_path, after_page, shared_anchors
    )

    diagnostics = []
    if len(proposals) < 2 and budget - used >= 2:
        try:
            adaptive = _pass0_regions(
                before_path,
                before_page,
                after_path,
                after_page,
                config,
                proposals,
                discipline,
            )
            used += 1
            proposals.extend(adaptive)
            diagnostics.append(
                {
                    "status": "generic_region_pass0",
                    "execution_state": "completed",
                    "proposals_added": len(adaptive),
                }
            )
        except Exception as exc:
            used += 1
            diagnostics.append(
                {
                    "status": "generic_region_pass0_error",
                    "execution_state": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    proposals = sorted(proposals, key=_proposal_priority)
    diagnostics.append(
        {
            "status": "generic_region_proposals",
            "regions": [
                {
                    "region_id": p.region_id,
                    "anchor_type": p.anchor_type,
                    "anchor_ids": list(p.anchor_ids),
                    "pair_confidence": round(p.pair_confidence, 4),
                    "pd_clip": p.pd_clip,
                    "rd_clip": p.rd_clip,
                    "provenance": p.provenance,
                }
                for p in proposals
            ],
        }
    )

    remaining = max(0, budget - used)
    discovery_slots = min(len(proposals), max(1, math.ceil(remaining / 2))) if remaining else 0
    discovered = []

    for region in proposals[:discovery_slots]:
        if used >= budget:
            break
        try:
            view = _view(
                before_path, before_page, after_path, after_page, region
            )
            used += 1
            p1 = _normalize_pass1(_call_pass1(view, config, discipline), region)
            discovered.append((region, view, p1))
        except Exception as exc:
            used += 1
            diagnostics.append(
                {
                    "status": "generic_region_pass1_error",
                    "execution_state": "failed",
                    "region_id": region.region_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    verify_order = sorted(
        [entry for entry in discovered if entry[2]["candidate_differences"]],
        key=lambda entry: (
            entry[2]["comparability"] != "high",
            -entry[0].pair_confidence,
            entry[0].region_id,
        ),
    )
    verification_by_region = {}
    for region, view, p1 in verify_order:
        if used >= budget:
            break
        try:
            used += 1
            verification_by_region[region.region_id] = _verify(
                view, p1, config, discipline
            )
        except Exception as exc:
            used += 1
            diagnostics.append(
                {
                    "status": "generic_region_verify_error",
                    "execution_state": "failed",
                    "region_id": region.region_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    findings = []
    for region, _view_data, p1 in discovered:
        verification = verification_by_region.get(region.region_id, [])
        accepted = []
        for idx, candidate in enumerate(p1["candidate_differences"]):
            verdict = (
                verification[idx]["verdict"]
                if idx < len(verification)
                else "unclear"
            )
            if verdict == "rejected":
                continue
            finding = {
                "label": (
                    "Инженерное визуальное различие"
                    if verdict == "confirmed"
                    else "Кандидат инженерного визуального различия"
                ),
                "change": candidate["description"],
                "rooms": [],
                "region_id": region.region_id,
                "anchor_type": region.anchor_type,
                "anchor_ids": list(region.anchor_ids),
                "anchor_provenance": region.provenance,
                "comparability": p1["comparability"],
                "verification": verdict,
                "verification_reason": (
                    verification[idx]["reason"] if idx < len(verification) else ""
                ),
                "differences": [candidate],
            }
            findings.append(finding)
            accepted.append(finding)

        diagnostics.append(
            {
                "status": (
                    "generic_region_significant"
                    if accepted
                    else (
                        "generic_region_unclear"
                        if p1["status"] == "unclear"
                        else "generic_region_no_candidate"
                    )
                ),
                "execution_state": "completed",
                "region_id": region.region_id,
                "anchor_type": region.anchor_type,
                "anchor_ids": list(region.anchor_ids),
                "anchor_provenance": region.provenance,
                "pair_confidence": region.pair_confidence,
                "comparability": p1["comparability"],
                "pd_clip": region.pd_clip,
                "rd_clip": region.rd_clip,
                "image_layout": "4: pd_general,pd_local,rd_general,rd_local",
                "general_max_dim": GENERAL_MAX_DIM,
                "local_max_dim": LOCAL_MAX_DIM,
                "pass1": p1,
                "verification": verification,
                "verification_executed": bool(verification),
                "findings": accepted,
            }
        )

    diagnostics.append(
        {
            "status": "generic_region_budget",
            "calls_used": used,
            "calls_budget": budget,
            "proposals_total": len(proposals),
            "regions_discovered": len(discovered),
            "regions_verified": len(verification_by_region),
            "discovery_first": True,
        }
    )
    return findings, diagnostics, used
