"""Blind high-recall comparison regions for drawings without reliable ROOM anchors.

Non-room anchors are navigation evidence only.  Axis/equipment labels and
vector-geometry clusters propose local regions, then GigaChat sees four
separate images (general/local PD and general/local RD).  PASS 1 may only
propose candidate differences; PASS 2 verifies them.  Raster or anchor scores
never prove a semantic change and never veto Vision by themselves.
"""
from __future__ import annotations

import hashlib
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
GENERAL_MAX_DIM = _int_env("NADZOR_GENERIC_GENERAL_MAX_DIM", 2400, 1200, 2800)
LOCAL_MAX_DIM = _int_env("NADZOR_GENERIC_LOCAL_MAX_DIM", 2600, 1400, 2800)

_PASS1 = f"""Ты — vision-аналитик инженерных чертежей. Слепо сравни одну
comparison_region ПД и РД/ИД. Регион может быть помещением, зоной осей,
оборудованием/системой или геометрическим кластером. Никаких legal/severity,
benchmark и expected elements.

Вход — 4 отдельных изображения: общий ПД, локальный ПД, общий РД/ИД,
локальный РД/ИД. Anchor metadata дан только для навигации и НЕ является
доказательством различия.

Сначала независимо перечисли видимые инженерные элементы и связи на ПД и РД.
Проверь inventory, topology, connections, parameters. Если типы графики
различаются, сопоставляй инженерные сущности/связи, а не координаты. При любом
наблюдаемом основании сохрани candidate difference; сомнение вынеси в
uncertainty_reasons. PASS 1 не имеет финального same.

comparability=high только если области действительно соответствуют друг другу
и инженерные элементы можно однозначно сопоставить; medium/low при слабой
привязке, неполной зоне, разном типе графики или плохой читаемости.

{UNTRUSTED_INPUT_RULE}

Ответ только JSON:
{{"region_id":"string","visibility_pd":"seen|partial|poor",
"visibility_rd":"seen|partial|poor","comparability":"high|medium|low",
"engineering_elements_pd":[],"engineering_elements_rd":[],
"connections_pd":[],"connections_rd":[],
"candidate_differences":[{{"category":"inventory|topology|connection|parameter|drawing_type_mismatch","description":"конкретный наблюдаемый факт","location_hint":"string|null"}}],
"uncertainty_reasons":[],"coverage":["inventory","topology","connections","parameters"],
"status":"changed_candidate|unchanged_candidate|unclear","summary":"кратко"}}"""

_VERIFY = f"""Ты — второй vision-pass. Проверь candidate_differences для одной
comparison_region по тем же 4 изображениям. Anchor metadata — только навигация.
Для каждого кандидата verdict confirmed|rejected|unclear. rejected разрешён
ТОЛЬКО при comparability=high и ясном визуальном доказательстве, что кандидат
ошибочен. При medium/low comparability возвращай unclear, не rejected.

{UNTRUSTED_INPUT_RULE}

Ответ только JSON:
{{"comparability":"high|medium|low","verified":[{{"idx":0,
"verdict":"confirmed|rejected|unclear","reason":"кратко","location":"string|null"}}],
"page_status":"changed|unchanged|unclear","status_notes":[]}}"""


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
        max(pr.x0, rect.x0 - max(pr.width * pad, w * 4)),
        max(pr.y0, rect.y0 - max(pr.height * pad, h * 6)),
        min(pr.x1, rect.x1 + max(pr.width * pad, w * 4)),
        min(pr.y1, rect.y1 + max(pr.height * pad, h * 6)),
    )
    return (rect.x0 / pr.width, rect.y0 / pr.height, rect.x1 / pr.width, rect.y1 / pr.height)


def _anchor_hit(path: str, page_no: int, key: str):
    try:
        doc = pymupdf.open(path)
        try:
            page = doc[page_no - 1]
            hits = []
            for term in anchor_search_terms(key):
                hits.extend(page.search_for(term))
            if not hits:
                return None
            pr = page.rect
            hits.sort(key=lambda r: (
                r.y0 > pr.height * .84,
                r.x0 > pr.width * .88,
                r.y0,
                r.x0,
            ))
            return _clip_fraction(page, hits[0])
        finally:
            doc.close()
    except Exception:
        return None


def _axis_region(path: str, page_no: int, keys: Sequence[str]):
    axis_keys = [key for key in keys if anchor_kind(key) == "axis"]
    if len(axis_keys) < 2:
        return None
    try:
        doc = pymupdf.open(path)
        try:
            page = doc[page_no - 1]
            pr = page.rect
            centers = []
            used = []
            for key in axis_keys[:8]:
                found = []
                for term in anchor_search_terms(key):
                    found.extend(page.search_for(term))
                if not found:
                    continue
                found.sort(key=lambda r: (r.y0 > pr.height * .9, r.x0 > pr.width * .92, r.y0, r.x0))
                hit = found[0]
                centers.append(((hit.x0 + hit.x1) / 2, (hit.y0 + hit.y1) / 2))
                used.append(key)
            if len(centers) < 2:
                return None
            xs = [p[0] for p in centers]
            ys = [p[1] for p in centers]
            rect = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
            # Axis labels often sit on the perimeter.  A minimum interior span
            # keeps the local view useful instead of collapsing to a label.
            if rect.width < pr.width * .20:
                cx = (rect.x0 + rect.x1) / 2
                rect.x0, rect.x1 = cx - pr.width * .18, cx + pr.width * .18
            if rect.height < pr.height * .20:
                cy = (rect.y0 + rect.y1) / 2
                rect.y0, rect.y1 = cy - pr.height * .18, cy + pr.height * .18
            rect &= pr
            return _clip_fraction(page, rect, pad=.08), tuple(used)
        finally:
            doc.close()
    except Exception:
        return None


def _rect_gap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0.0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0.0)
    return math.hypot(dx, dy)


def _geometry_clusters(path: str, page_no: int, limit: int = 5):
    """Return deterministic vector clusters and coordinate-free fingerprints."""
    try:
        doc = pymupdf.open(path)
        try:
            page = doc[page_no - 1]
            pr = page.rect
            page_area = max(1.0, pr.width * pr.height)
            rects = []
            for drawing in page.get_drawings():
                raw = drawing.get("rect")
                if not raw:
                    continue
                rect = pymupdf.Rect(raw)
                area = max(0.0, rect.width * rect.height)
                if area <= page_area * 0.000002 or area >= page_area * .35:
                    continue
                rects.append(rect)
            rects = rects[:320]
            if len(rects) < 3:
                return []
            proximity = max(pr.width, pr.height) * .012
            parent = list(range(len(rects)))

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            for i, a in enumerate(rects):
                for j in range(i + 1, min(len(rects), i + 48)):
                    if _rect_gap(a, rects[j]) <= proximity:
                        union(i, j)
            groups = {}
            for i, rect in enumerate(rects):
                groups.setdefault(find(i), []).append(rect)
            out = []
            for group in groups.values():
                if len(group) < 3:
                    continue
                bbox = group[0]
                for rect in group[1:]:
                    bbox |= rect
                frac_area = bbox.width * bbox.height / page_area
                if not .001 <= frac_area <= .32:
                    continue
                density = min(1.0, sum(max(1.0, r.width * r.height) for r in group) / max(1.0, bbox.width * bbox.height))
                aspect = bbox.width / max(1.0, bbox.height)
                fp = (min(1.0, len(group) / 80.0), min(4.0, aspect) / 4.0, density, min(.32, frac_area) / .32)
                score = len(group) * (.4 + density)
                clip = _clip_fraction(page, bbox, pad=.08)
                out.append((score, fp, clip))
            out.sort(key=lambda row: -row[0])
            return out[:limit]
        finally:
            doc.close()
    except Exception:
        return []


def _fp_distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / max(1, len(a)))


def propose_regions(before_path: str, before_page: int, after_path: str, after_page: int, shared_anchors: Sequence[str]):
    proposals: list[RegionProposal] = []

    # Strong equipment/system labels can localize themselves directly.
    for key in shared_anchors:
        if anchor_kind(key) != "equipment_system":
            continue
        pd_clip = _anchor_hit(before_path, before_page, key)
        rd_clip = _anchor_hit(after_path, after_page, key)
        if pd_clip and rd_clip:
            proposals.append(RegionProposal(
                region_id=f"equipment_system:{anchor_value(key)}",
                anchor_type="equipment_system",
                anchor_ids=(key,),
                pd_clip=pd_clip,
                rd_clip=rd_clip,
                pair_confidence=.78,
                provenance="deterministic_text_anchor",
            ))
        if len(proposals) >= 2:
            break

    # Axes are useful only as a multi-axis signature, never as one weak token.
    pd_axis = _axis_region(before_path, before_page, shared_anchors)
    rd_axis = _axis_region(after_path, after_page, shared_anchors)
    if pd_axis and rd_axis:
        pd_clip, pd_used = pd_axis
        rd_clip, rd_used = rd_axis
        used = tuple(key for key in pd_used if key in set(rd_used))
        if len(used) >= 2:
            proposals.append(RegionProposal(
                region_id="axis_zone:" + "+".join(anchor_value(key) for key in used[:4]),
                anchor_type="axis",
                anchor_ids=used[:4],
                pd_clip=pd_clip,
                rd_clip=rd_clip,
                pair_confidence=.72,
                provenance="deterministic_axis_signature",
            ))

    # Geometry is the generic no-text fallback.  It proposes navigation only.
    if len(proposals) < MAX_GEOMETRY_REGIONS_PER_PAIR:
        pd_clusters = _geometry_clusters(before_path, before_page)
        rd_clusters = _geometry_clusters(after_path, after_page)
        used_rd = set()
        matched = []
        for pi, (_, pfp, pclip) in enumerate(pd_clusters):
            choices = [( _fp_distance(pfp, rfp), ri, rclip) for ri, (_, rfp, rclip) in enumerate(rd_clusters) if ri not in used_rd]
            if not choices:
                continue
            distance, ri, rclip = min(choices)
            if distance > .34:
                continue
            used_rd.add(ri)
            matched.append((distance, pi, pclip, ri, rclip))
        matched.sort()
        for distance, pi, pclip, ri, rclip in matched:
            if len(proposals) >= MAX_GEOMETRY_REGIONS_PER_PAIR:
                break
            proposals.append(RegionProposal(
                region_id=f"geometry:{pi}-{ri}",
                anchor_type="geometry",
                anchor_ids=(),
                pd_clip=pclip,
                rd_clip=rclip,
                pair_confidence=max(.35, .75 - distance),
                provenance="deterministic_geometry_fingerprint",
            ))
    return proposals


def _view(before_path: str, before_page: int, after_path: str, after_page: int, region: RegionProposal):
    return {
        "region": region,
        "pd_general": render_page_to_png_bytes(before_path, before_page, max_dim=GENERAL_MAX_DIM),
        "pd_local": render_page_to_png_bytes(before_path, before_page, max_dim=LOCAL_MAX_DIM, clip_frac=region.pd_clip),
        "rd_general": render_page_to_png_bytes(after_path, after_page, max_dim=GENERAL_MAX_DIM),
        "rd_local": render_page_to_png_bytes(after_path, after_page, max_dim=LOCAL_MAX_DIM, clip_frac=region.rd_clip),
    }


def _images(view):
    return [png_bytes_to_data_url(view[key]) for key in ("pd_general", "pd_local", "rd_general", "rd_local")]


def _call_pass1(view, config: LlmConfig, discipline: str):
    region: RegionProposal = view["region"]
    text = (
        f"region_id={region.region_id}; anchor_type={region.anchor_type}; "
        f"anchor_ids={list(region.anchor_ids)}; pair_confidence={region.pair_confidence:.3f}; "
        f"discipline={discipline or 'unknown'}. Anchor metadata — navigation only."
    )
    digest = hashlib.sha256((region.region_id + text).encode()).hexdigest()
    out = call_llm_json(config, _PASS1, text, images=_images(view), operation="vision", source_digest=digest, prompt_version="generic-region-pass1-v1")
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
        candidates.append({
            "category": str(item.get("category") or "topology"),
            "description": description,
            "location_hint": str(item.get("location_hint") or "").strip() or None,
        })
    status = "changed_candidate" if candidates else str(raw.get("status") or "unclear").casefold()
    if status not in {"changed_candidate", "unchanged_candidate", "unclear"}:
        status = "unclear"
    return {
        "region_id": region.region_id,
        "anchor_type": region.anchor_type,
        "anchor_ids": list(region.anchor_ids),
        "pair_confidence": region.pair_confidence,
        "comparability": comparability,
        "visibility_pd": str(raw.get("visibility_pd") or "partial"),
        "visibility_rd": str(raw.get("visibility_rd") or "partial"),
        "engineering_elements_pd": raw.get("engineering_elements_pd") or [],
        "engineering_elements_rd": raw.get("engineering_elements_rd") or [],
        "connections_pd": raw.get("connections_pd") or [],
        "connections_rd": raw.get("connections_rd") or [],
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
    import json
    region: RegionProposal = view["region"]
    text = (
        f"region_id={region.region_id}; PASS1 comparability={pass1['comparability']}; "
        f"candidates={json.dumps(candidates, ensure_ascii=False)}"
    )
    digest = hashlib.sha256((region.region_id + text).encode()).hexdigest()
    raw = call_llm_json(config, _VERIFY, text, images=_images(view), operation="vision", source_digest=digest, prompt_version="generic-region-verify-v1")
    raw = raw if isinstance(raw, dict) else {}
    verifier_comp = str(raw.get("comparability") or pass1["comparability"]).casefold()
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
        # Fail closed on semantic rejection: low/medium comparability cannot
        # erase a high-recall candidate.
        if verdict == "rejected" and (pass1["comparability"] != "high" or verifier_comp != "high"):
            verdict = "unclear"
        found[idx] = {
            "idx": idx,
            "verdict": verdict,
            "reason": str(item.get("reason") or ""),
            "location": item.get("location"),
            "comparability": verifier_comp,
        }
    return [found.get(i, {"idx": i, "verdict": "unclear", "reason": "verification omitted candidate", "location": None, "comparability": verifier_comp}) for i in range(len(candidates))]


def compare_non_room_regions(before_path: str, before_page: int, after_path: str, after_page: int, shared_anchors: Sequence[str], config: LlmConfig, *, max_calls: int, discipline: str = ""):
    if max_calls <= 0:
        return [], [], 0
    proposals = propose_regions(before_path, before_page, after_path, after_page, shared_anchors)
    diagnostics = [{
        "status": "generic_region_proposals",
        "regions": [{
            "region_id": p.region_id,
            "anchor_type": p.anchor_type,
            "anchor_ids": list(p.anchor_ids),
            "pair_confidence": round(p.pair_confidence, 4),
            "pd_clip": p.pd_clip,
            "rd_clip": p.rd_clip,
            "provenance": p.provenance,
        } for p in proposals],
    }]
    findings = []
    used = 0
    budget = min(max_calls, MAX_GENERIC_CALLS_PER_PAIR)
    for region in proposals:
        if used >= budget:
            break
        try:
            view = _view(before_path, before_page, after_path, after_page, region)
            used += 1
            p1 = _normalize_pass1(_call_pass1(view, config, discipline), region)
        except Exception as exc:
            diagnostics.append({"status": "generic_region_pass1_error", "region_id": region.region_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        verification = []
        if p1["candidate_differences"] and used < budget:
            used += 1
            try:
                verification = _verify(view, p1, config, discipline)
            except Exception as exc:
                diagnostics.append({"status": "generic_region_verify_error", "region_id": region.region_id, "error": f"{type(exc).__name__}: {exc}"})
        accepted = []
        for idx, candidate in enumerate(p1["candidate_differences"]):
            verdict = verification[idx]["verdict"] if idx < len(verification) else "unclear"
            if verdict == "rejected":
                continue
            finding = {
                "label": "Инженерное визуальное различие" if verdict == "confirmed" else "Кандидат инженерного визуального различия",
                "change": candidate["description"],
                "rooms": [],
                "region_id": region.region_id,
                "anchor_type": region.anchor_type,
                "anchor_ids": list(region.anchor_ids),
                "comparability": p1["comparability"],
                "verification": verdict,
                "verification_reason": verification[idx]["reason"] if idx < len(verification) else "",
                "differences": [candidate],
            }
            findings.append(finding)
            accepted.append(finding)
        diagnostics.append({
            "status": "generic_region_significant" if accepted else ("generic_region_unclear" if p1["status"] == "unclear" else "generic_region_no_candidate"),
            "region_id": region.region_id,
            "anchor_type": region.anchor_type,
            "anchor_ids": list(region.anchor_ids),
            "pair_confidence": region.pair_confidence,
            "comparability": p1["comparability"],
            "image_layout": "4: pd_general,pd_local,rd_general,rd_local",
            "pass1": p1,
            "verification": verification,
            "findings": accepted,
        })
    return findings, diagnostics, used
