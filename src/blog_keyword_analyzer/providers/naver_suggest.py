from __future__ import annotations

from typing import Iterable, List

from ..http import HttpClient
from ..text_utils import normalize_query, unique_ordered


class NaverSuggestProvider:
    BASE_URL = "https://ac.search.naver.com/nx/ac"

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    def suggest(self, seed: str) -> List[str]:
        params = {
            "q": seed,
            "st": 100,
            "r_format": "json",
            "r_enc": "UTF-8",
            "r_unicode": 0,
            "t_koreng": 1,
            "q_enc": "UTF-8",
        }
        data = self.http.get_json(self.BASE_URL, params=params)
        out: List[str] = []
        try:
            items = data.get("items") if isinstance(data, dict) else None
            if isinstance(items, list):
                for block in items:
                    if isinstance(block, list) and len(block) >= 2 and isinstance(block[1], list):
                        for s in block[1]:
                            if isinstance(s, str):
                                out.append(s)
        except Exception:
            pass
        cleaned = [normalize_query(s) for s in out]
        return unique_ordered([s for s in cleaned if s and s != seed])

    def bulk_suggest(self, seeds: Iterable[str]) -> List[str]:
        out: List[str] = []
        for s in seeds:
            out.extend(self.suggest(s))
        return unique_ordered(out)

