from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


@dataclass
class MonetizationParams:
    capture_pct: float = 15.0  # % of search traffic you capture
    pv_per_visit: float = 1.3  # pageviews per visitor
    ecpm: float = 2500.0  # KRW per 1000 pageviews (display ads)
    aff_cvr_pct: float = 1.5  # % visitors who convert via affiliate
    aff_commission: float = 1500.0  # KRW commission per order


INTENT_RULES = {
    "transactional": {
        "tokens": [
            "구매",
            "가격",
            "할인",
            "최저가",
            "쿠폰",
            "예약",
            "신청",
            "견적",
            "비용",
            "수강",
        ]
    },
    "commercial": {
        "tokens": [
            "추천",
            "비교",
            "후기",
            "리뷰",
            "장단점",
            "베스트",
            "top",
            "vs",
        ]
    },
    "informational": {
        "tokens": [
            "방법",
            "사용법",
            "가이드",
            "주의",
            "꿀팁",
            "하는 법",
            "어떻게",
            "왜",
            "무엇",
        ]
    },
}


def classify_intent(keyword: str) -> str:
    k = (keyword or "").lower()
    for label in ("transactional", "commercial", "informational"):
        for t in INTENT_RULES[label]["tokens"]:
            if t in keyword:
                return label
            if t.lower() in k:
                return label
    return "informational"


def _monthly_from_metrics(m) -> int:
    try:
        pc = int(getattr(m, "naver_monthly_pc", 0) or 0)
        mo = int(getattr(m, "naver_monthly_mobile", 0) or 0)
        return max(pc + mo, 0)
    except Exception:
        return 0


def monetize_keywords(
    keywords: Iterable[str],
    metrics: Dict[str, object],
    params: Optional[MonetizationParams] = None,
    min_monthly: int = 0,
    exclude_tokens: Optional[Iterable[str]] = None,
) -> List[dict]:
    """Compute monetization estimates per keyword.

    - Uses Naver monthly PC/Mobile search volume from metrics.
    - Computes display ads revenue via eCPM and affiliate revenue via CVR/commission.
    - Returns list of dict rows sorted by total revenue desc.
    """
    params = params or MonetizationParams()
    ex_set = {t.strip() for t in (exclude_tokens or []) if t and t.strip()}

    rows: List[dict] = []
    for kw in keywords:
        m = metrics.get(kw)
        if not m:
            continue
        monthly = _monthly_from_metrics(m)
        if monthly < int(min_monthly):
            continue
        if ex_set and any(tok in kw for tok in ex_set):
            continue

        capture = max(0.0, min(100.0, float(params.capture_pct))) / 100.0
        visits = monthly * capture
        pv = visits * float(params.pv_per_visit)
        display_rev = (pv / 1000.0) * float(params.ecpm)
        aff_orders = visits * (max(0.0, float(params.aff_cvr_pct)) / 100.0)
        aff_rev = aff_orders * float(params.aff_commission)
        total = display_rev + aff_rev
        cpc = getattr(m, "naver_cpc", None)

        rows.append(
            {
                "keyword": kw,
                "intent": classify_intent(kw),
                "monthly_search": monthly,
                "capture_pct": round(params.capture_pct, 2),
                "pv_per_visit": round(params.pv_per_visit, 3),
                "eCPM": round(params.ecpm, 2),
                "aff_cvr_pct": round(params.aff_cvr_pct, 3),
                "aff_commission": round(params.aff_commission, 2),
                "est_visits": int(visits),
                "est_pageviews": int(pv),
                "est_display_rev": int(display_rev),
                "est_aff_rev": int(aff_rev),
                "est_total_rev": int(total),
                "naver_cpc": float(cpc) if isinstance(cpc, (int, float)) else None,
            }
        )

    rows.sort(key=lambda r: (r.get("est_total_rev", 0), r.get("monthly_search", 0)), reverse=True)
    return rows

