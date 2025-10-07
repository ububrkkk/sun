from __future__ import annotations

from typing import Iterable, List

from ..http import HttpClient
from ..text_utils import normalize_query, unique_ordered


class GoogleSuggestProvider:
    BASE_URL = "https://suggestqueries.google.com/complete/search"

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    def suggest(self, seed: str, hl: str = "ko") -> List[str]:
        data = self.http.get_json(self.BASE_URL, params={"client": "firefox", "q": seed, "hl": hl})
        if not isinstance(data, list) or len(data) < 2:
            return []
        suggestions = data[1] or []
        cleaned = [normalize_query(s) for s in suggestions if isinstance(s, str)]
        return unique_ordered([s for s in cleaned if s and s != seed])

    def bulk_suggest(self, seeds: Iterable[str], hl: str = "ko") -> List[str]:
        out: List[str] = []
        for s in seeds:
            out.extend(self.suggest(s, hl=hl))
        return unique_ordered(out)

