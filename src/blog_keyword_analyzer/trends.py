from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Set, Tuple


def default_hot_terms() -> List[str]:
    return [
        "핫플",
        "뉴오픈",
        "오픈런",
        "웨이팅",
        "예약",
        "오션뷰",
        "바다뷰",
        "루프탑",
        "야경",
        "브런치",
        "디저트",
        "가성비",
        "무료주차",
        "노키즈존",
        "애견동반",
        "포장",
        "배달",
    ]


@dataclass
class TrendDelta:
    new_suggestions: List[str]
    dropped_suggestions: List[str]
    hot_terms: List[Tuple[str, int]]


def compute_trends(prev: Iterable[str], curr: Iterable[str], hot_terms: List[str] | None = None) -> TrendDelta:
    prev_set: Set[str] = set(prev)
    curr_set: Set[str] = set(curr)
    new_items = sorted(curr_set - prev_set)
    dropped = sorted(prev_set - curr_set)
    terms = hot_terms or default_hot_terms()
    counts = {t: 0 for t in terms}
    for s in curr_set:
        for t in terms:
            if t in s:
                counts[t] += 1
    hot_sorted = sorted([(k, v) for k, v in counts.items() if v > 0], key=lambda x: x[1], reverse=True)
    return TrendDelta(new_suggestions=new_items, dropped_suggestions=dropped, hot_terms=hot_sorted)

