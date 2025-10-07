from __future__ import annotations

from typing import Iterable, List

from .text_utils import normalize_query, unique_ordered

KOREAN_LONGTAIL_SUFFIXES = [
    "방법", "후기", "리뷰", "비교", "추천", "가격", "주의사항", "장점", "단점", "가성비",
]


def append_suffixes(seed: str, suffixes: Iterable[str] | None = None) -> List[str]:
    suffixes = list(suffixes) if suffixes is not None else KOREAN_LONGTAIL_SUFFIXES
    return unique_ordered([normalize_query(f"{seed} {s}") for s in suffixes])


def expand_with_suffixes(seeds: Iterable[str], suffixes: Iterable[str] | None = None) -> List[str]:
    out: List[str] = []
    for s in seeds:
        out.extend(append_suffixes(s, suffixes=suffixes))
    return unique_ordered(out)


PROFILE_SUFFIXES = {
    "travel": ["여행", "일정", "주차", "렌터카", "야경", "숙소", "카페", "맛집", "예산"],
    "food": ["맛집", "카페", "브런치", "디저트", "메뉴", "가격", "예약", "웨이팅", "주차"],
}


def expand_with_profile(seeds: Iterable[str], profile: str) -> List[str]:
    return expand_with_suffixes(seeds, PROFILE_SUFFIXES.get(profile.lower(), []))

