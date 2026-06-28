"""Pure-Python business intelligence — math that turns data into decisions.

No ML, no external services. Every function here is pure (data in, insight out)
so it's trivially testable. Crucially, the inputs match the **real** NUMU API
shapes our client returns:

  * order list items carry ``customer_id``, ``customer_name``, ``total`` (integer
    minor units / cents), ``created_at`` — and do NOT carry line items, so we
    never try to read them.
  * product list items carry ``price`` (decimal string, major units),
    ``quantity``, ``status``.
  * per-product sales **velocity** comes from the analytics ``top-products``
    endpoint (``quantity_sold`` over a window), not from iterating orders.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * p)
    return sorted_values[min(idx, len(sorted_values) - 1)]


# ── RFM customer segmentation ─────────────────────────────────────────────────

# Segment rules ordered most-valuable → least. First match wins.
def _segment_for(r: int, f: int, m: int) -> str:
    if r >= 4 and f >= 4 and m >= 4:
        return "champions"
    if r <= 2 and f >= 4 and m >= 4:
        return "cannot_lose"
    if r >= 4 and f >= 3:
        return "loyal_customers"
    if r >= 4 and f <= 2 and m <= 2:
        return "new_customers"
    if r >= 3 and f >= 3:
        return "potential_loyalists"
    if r == 3:
        return "need_attention"
    if r <= 2 and f >= 3:
        return "at_risk"
    if r <= 2 and m >= 4:
        return "about_to_sleep"
    if r <= 1:
        return "lost"
    return "hibernating"


def compute_rfm_segments(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Score customers by Recency/Frequency/Monetary from the order list.

    Monetary is kept in integer minor units (cents) — the caller formats it.
    Only paid/active orders should ideally be passed; we count whatever is given.
    """
    now = datetime.now(UTC)
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"frequency": 0, "monetary": 0, "last": None, "name": None}
    )

    for o in orders:
        cid = o.get("customer_id")
        if not cid:
            continue
        dt = _parse_dt(o.get("created_at"))
        s = stats[cid]
        s["frequency"] += 1
        s["monetary"] += int(o.get("total") or 0)
        s["name"] = s["name"] or o.get("customer_name")
        if dt and (s["last"] is None or dt > s["last"]):
            s["last"] = dt

    if not stats:
        return {"segments": {}, "customers_scored": 0}

    for s in stats.values():
        s["recency_days"] = (now - s["last"]).days if s["last"] else 9999

    recencies = sorted(s["recency_days"] for s in stats.values())
    frequencies = sorted(s["frequency"] for s in stats.values())
    monetaries = sorted(s["monetary"] for s in stats.values())

    def score_recency(days: int) -> int:
        # Lower days = better, so invert the percentile bands.
        for band, pts in ((0.2, 5), (0.4, 4), (0.6, 3), (0.8, 2)):
            if days <= _percentile(recencies, band):
                return pts
        return 1

    def score_band(value: int, values: list[float]) -> int:
        for band, pts in ((0.8, 5), (0.6, 4), (0.4, 3), (0.2, 2)):
            if value >= _percentile(values, band):
                return pts
        return 1

    segments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cid, s in stats.items():
        r = score_recency(s["recency_days"])
        f = score_band(s["frequency"], frequencies)
        m = score_band(s["monetary"], monetaries)
        segments[_segment_for(r, f, m)].append(
            {
                "customer_id": cid,
                "name": s["name"] or "Unknown",
                "rfm": f"{r}{f}{m}",
                "recency_days": s["recency_days"],
                "frequency": s["frequency"],
                "monetary_cents": s["monetary"],
            }
        )

    # Sort each segment by monetary desc.
    for members in segments.values():
        members.sort(key=lambda x: x["monetary_cents"], reverse=True)

    return {"segments": dict(segments), "customers_scored": len(stats)}


# ── Inventory health ──────────────────────────────────────────────────────────

def classify_inventory_health(
    inventory_items: list[dict[str, Any]],
    velocity_per_day: dict[str, float],
) -> dict[str, list[dict[str, Any]]]:
    """Bucket products into critical / low / healthy / overstock.

    ``velocity_per_day`` maps product id → units sold per day (from analytics).
    """
    health: dict[str, list[dict[str, Any]]] = {
        "critical": [],
        "low": [],
        "healthy": [],
        "overstock": [],
    }
    for it in inventory_items:
        pid = it.get("id")
        stock = int(it.get("quantity") or 0)
        v = float(velocity_per_day.get(pid, 0.0))
        days_cover = round(stock / v, 1) if v > 0 else None
        record = {
            "id": pid,
            "name": it.get("name"),
            "sku": it.get("sku"),
            "quantity": stock,
            "velocity_per_day": round(v, 2),
            "days_of_cover": days_cover,
        }
        if it.get("is_out_of_stock") or stock == 0 or (v > 0 and days_cover is not None and days_cover < 3):
            health["critical"].append(record)
        elif it.get("is_low_stock") or (v > 0 and days_cover is not None and days_cover < 7):
            health["low"].append(record)
        elif v == 0 and stock > 100:
            health["overstock"].append(record)
        else:
            health["healthy"].append(record)
    return health


# ── Price suggestions (advisory, rule-based) ──────────────────────────────────

def suggest_price_adjustments(
    products: list[dict[str, Any]],
    velocity_per_day: dict[str, float],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Rule-based price suggestions from velocity + stock. Advisory only."""
    suggestions: list[dict[str, Any]] = []
    for p in products:
        pid = p.get("id")
        try:
            current = float(p.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if current <= 0:
            continue
        stock = int(p.get("quantity") or 0)
        v = float(velocity_per_day.get(pid, 0.0))

        suggested: float | None = None
        reason: str | None = None
        if v > 1 and stock < 10:
            suggested = round(current * 1.10, 2)
            reason = (
                f"Strong demand ({v:.1f}/day) but only {stock} in stock — a ~10% "
                "rise captures margin while it's scarce."
            )
        elif v == 0 and stock > 50:
            suggested = round(current * 0.85, 2)
            reason = (
                f"No recent sales with {stock} units sitting — a ~15% markdown "
                "helps clear dead stock."
            )

        if suggested is not None and suggested != current:
            suggestions.append(
                {
                    "id": pid,
                    "name": p.get("name"),
                    "current_price": current,
                    "suggested_price": suggested,
                    "stock": stock,
                    "velocity_per_day": round(v, 2),
                    "reason": reason,
                }
            )

    suggestions.sort(
        key=lambda x: abs(x["suggested_price"] - x["current_price"])
        * max(x["velocity_per_day"], 0.1),
        reverse=True,
    )
    return suggestions[:limit]


def velocity_from_top_products(
    top_products: list[dict[str, Any]], window_days: int
) -> dict[str, float]:
    """Turn analytics top-products (quantity_sold over a window) into per-day."""
    if window_days <= 0:
        window_days = 1
    out: dict[str, float] = {}
    for tp in top_products:
        pid = tp.get("id")
        if not pid:
            continue
        out[pid] = float(tp.get("quantity_sold") or 0) / window_days
    return out
