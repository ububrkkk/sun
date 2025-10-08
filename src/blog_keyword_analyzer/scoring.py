from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Iterable, List

from .text_utils import tokenize


@dataclass
class KeywordScore:
    keyword: str
    demand: float
    competition: float
    opportunity: float
    provider_hits: int


def estimate_demand_score(q: str, provider_hits: int = 1) -> float:
    n = len(tokenize(q))
    base = 1.0 if 2 <= n <= 5 else 0.6
    base *= 1.0 + min(provider_hits, 5) * 0.1
    return min(base, 3.0)


def estimate_competition_score(q: str) -> float:
    n = len(tokenize(q))
    if n <= 1:
        return 2.0
    return max(0.5, 1.5 - min(n - 2, 4) * 0.12)


def score_keywords(keywords: Iterable[str], hit_counts: Dict[str, int] | None = None) -> List[KeywordScore]:
    results: List[KeywordScore] = []
    hit_counts = hit_counts or {}
    for kw in keywords:
        hits = hit_counts.get(kw, 1)
        d = estimate_demand_score(kw, provider_hits=hits)
        c = estimate_competition_score(kw)
        opp = max(d * 1.4 - c, 0.0)
        results.append(KeywordScore(kw, round(d, 3), round(c, 3), round(opp, 3), hits))
    results.sort(key=lambda x: (x.opportunity, x.demand), reverse=True)
    return results


def _comp_from_results(total: int) -> float:
    if total <= 0:
        return 0.8
    return max(0.6, min(2.3, 0.7 + (math.log10(total + 1) * 0.25)))


def score_keywords_with_metrics(
    keywords: Iterable[str],
    hit_counts: Dict[str, int] | None,
    metrics: Dict[str, object],
) -> List[KeywordScore]:
    results: List[KeywordScore] = []
    hit_counts = hit_counts or {}
    for kw in keywords:
        hits = hit_counts.get(kw, 1)
        d = estimate_demand_score(kw, provider_hits=hits)
        c = estimate_competition_score(kw)
        m = metrics.get(kw)
        try:
            monthly = (getattr(m, "naver_monthly_pc", 0) or 0) + (getattr(m, "naver_monthly_mobile", 0) or 0)
            if monthly > 0:
                d = min(3.0, 0.6 + math.log10(1 + monthly) * 0.6 + min(hits, 5) * 0.05)
            nav_tot = getattr(m, "naver_blog_total", None)
            g_tot = getattr(m, "google_total", None)
            comps = []
            if isinstance(nav_tot, int):
                comps.append(_comp_from_results(nav_tot))
            if isinstance(g_tot, int):
                comps.append(_comp_from_results(g_tot))
            if comps:
                c = max(0.5, min(sum(comps) / len(comps), 2.5))
        except Exception:
            pass
        opp = max(d * 1.4 - c, 0.0)
        results.append(KeywordScore(kw, round(d, 3), round(c, 3), round(opp, 3), hits))
    results.sort(key=lambda x: (x.opportunity, x.demand), reverse=True)
    return results


def score_keywords_by_platform(
    keywords: Iterable[str],
    hit_counts: Dict[str, int] | None,
    metrics: Dict[str, object] | None,
    platform: str = "naver",
) -> List[KeywordScore]:
    # Simple wrapper: if metrics available, use them, else heuristic
    if metrics:
        return score_keywords_with_metrics(keywords, hit_counts, metrics)
    return score_keywords(keywords, hit_counts)

