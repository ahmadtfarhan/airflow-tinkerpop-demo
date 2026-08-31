"""Deterministic scoring, shared by every arm.

Set-based precision/recall/F1 against known gold answers -- no LLM judge.
Unresolvable predictions count as false positives rather than being dropped.
Headline metric is macro-F1 across buckets; micro-F1 is also reported.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, Iterable, List, Sequence

BUCKET_ORDER = ["B1", "B2", "B3", "B4", "B5"]


def normalize(
    values: Iterable[str],
    aliases: Dict[str, str],
    *,
    fuzzy: bool = True,
) -> List[str]:
    """Map predicted surface forms onto canonical entity ids.

    Exact alias lookup, then a conservative close-match fallback so that
    "Northwind Analytics." or "the Northwind Analytics account" still resolve.
    Anything that still fails to resolve is returned as-is and counts against
    precision.
    """
    keys = list(aliases)
    out: List[str] = []
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip().strip(".,;:'\"")
        if not text:
            continue
        key = text.casefold()
        if key in aliases:
            out.append(aliases[key])
            continue
        # Strip common wrappers: "the X account", "X (C059)".
        stripped = re.sub(r"^(the|account|customer|resource|group)\s+", "", key)
        stripped = re.sub(r"\s*\([^)]*\)$", "", stripped).strip()
        if stripped in aliases:
            out.append(aliases[stripped])
            continue
        if fuzzy:
            match = difflib.get_close_matches(stripped or key, keys, n=1, cutoff=0.92)
            if match:
                out.append(aliases[match[0]])
                continue
        out.append(text)  # unresolved -> counts as a false positive
    return out


def _dedupe(values: Sequence[str]) -> List[str]:
    seen, out = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _prf(gold: Sequence[str], pred: Sequence[str]) -> Dict[str, float]:
    g, p = set(gold), set(pred)
    if not g and not p:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not p:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    if not g:
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}
    tp = len(g & p)
    precision = tp / len(p)
    recall = tp / len(g)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def _scalar_match(gold: str | None, pred: str | None) -> float:
    """Exact match on the meaningful content; numeric answers compared as numbers."""
    if gold is None:
        return 1.0
    if pred is None:
        return 0.0
    g, p = str(gold).strip().casefold(), str(pred).strip().casefold()
    if g == p:
        return 1.0
    g_nums = re.findall(r"-?\d+", g)
    p_nums = re.findall(r"-?\d+", p)
    if g_nums:
        return 1.0 if p_nums == g_nums else 0.0

    # Containment match: every gold component must appear as a whole word,
    # tolerating verbose phrasing without tolerating a wrong answer.
    g_parts = [x.strip() for x in g.split(",") if x.strip()]
    if g_parts and all(re.search(rf"\b{re.escape(part)}\b", p) for part in g_parts):
        return 1.0
    return 0.0


def _sequence_similarity(gold: Sequence[str], pred: Sequence[str]) -> float:
    """1.0 for an identical ordering, degrading with edit distance."""
    if not gold:
        return 1.0
    return difflib.SequenceMatcher(a=list(gold), b=list(pred)).ratio()


def score_one(
    question: Dict[str, Any],
    answer: Any,
    aliases: Dict[str, str],
) -> Dict[str, Any]:
    """Score one arm's answer to one question."""
    from include.schemas import as_answer

    ans = as_answer(answer)
    gold_ids = question.get("gold_entity_ids") or []
    ordered = bool(question.get("ordered"))

    pred_ids = _dedupe(normalize(ans.entity_ids, aliases))
    gold_norm = list(gold_ids)

    metrics = _prf(gold_norm, pred_ids)
    metrics["exact"] = 1.0 if set(gold_norm) == set(pred_ids) else 0.0
    if ordered:
        metrics["exact"] = 1.0 if gold_norm == pred_ids else 0.0
        metrics["sequence"] = _sequence_similarity(gold_norm, pred_ids)

    if question.get("gold_scalar") is not None and not gold_ids:
        # Scalar-only question: the scalar is the score.
        metrics["scalar_exact"] = _scalar_match(question["gold_scalar"], ans.scalar)
        metrics["f1"] = metrics["scalar_exact"]
        metrics["precision"] = metrics["scalar_exact"]
        metrics["recall"] = metrics["scalar_exact"]
        metrics["exact"] = metrics["scalar_exact"]
    elif question.get("gold_scalar") is not None:
        metrics["scalar_exact"] = _scalar_match(question["gold_scalar"], ans.scalar)

    return {
        "question_id": question["id"],
        "bucket": question["bucket"],
        "gold": gold_norm,
        "gold_scalar": question.get("gold_scalar"),
        "predicted": pred_ids,
        "predicted_scalar": ans.scalar,
        "confidence": ans.confidence,
        **metrics,
    }


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-bucket means plus both headline averages."""
    by_arm: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        arm = row.get("arm", "unknown")
        by_arm.setdefault(arm, {"rows": []})["rows"].append(row)

    summary: Dict[str, Any] = {"arms": {}}
    for arm, payload in by_arm.items():
        arm_rows = payload["rows"]
        buckets: Dict[str, Dict[str, float]] = {}
        for bucket in BUCKET_ORDER:
            sel = [r for r in arm_rows if r["bucket"] == bucket]
            if not sel:
                continue
            buckets[bucket] = {
                "n": len(sel),
                "f1": sum(r["f1"] for r in sel) / len(sel),
                "exact": sum(r["exact"] for r in sel) / len(sel),
                "latency_p50": _percentile([r.get("latency_s", 0.0) for r in sel], 50),
                "latency_p95": _percentile([r.get("latency_s", 0.0) for r in sel], 95),
                "tokens": sum(r.get("tokens", 0) for r in sel),
            }
        macro = sum(b["f1"] for b in buckets.values()) / len(buckets) if buckets else 0.0
        micro = sum(r["f1"] for r in arm_rows) / len(arm_rows) if arm_rows else 0.0
        summary["arms"][arm] = {
            "buckets": buckets,
            "macro_f1": macro,
            "micro_f1": micro,
            "questions": len(arm_rows),
            "total_tokens": sum(r.get("tokens", 0) for r in arm_rows),
        }
    return summary


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def markdown_table(summary: Dict[str, Any]) -> str:
    """Renders in the Airflow UI's XCom view."""
    arms = list(summary["arms"])
    lines = ["| bucket | " + " | ".join(arms) + " |",
             "|---|" + "---|" * len(arms)]
    for bucket in BUCKET_ORDER:
        cells = []
        for arm in arms:
            b = summary["arms"][arm]["buckets"].get(bucket)
            cells.append(f"{b['f1']:.2f}" if b else "-")
        if any(c != "-" for c in cells):
            lines.append(f"| {bucket} | " + " | ".join(cells) + " |")
    lines.append("| **macro-F1** | " +
                 " | ".join(f"**{summary['arms'][a]['macro_f1']:.2f}**" for a in arms) + " |")
    lines.append("| micro-F1 | " +
                 " | ".join(f"{summary['arms'][a]['micro_f1']:.2f}" for a in arms) + " |")
    return "\n".join(lines)
