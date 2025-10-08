from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from .http import HttpClient


@dataclass
class EnrichedMetrics:
    keyword: str
    naver_blog_total: Optional[int] = None
    google_total: Optional[int] = None
    naver_monthly_pc: Optional[int] = None
    naver_monthly_mobile: Optional[int] = None
    naver_cpc: Optional[float] = None


class NaverOpenApiEnricher:
    BASE_URL = "https://openapi.naver.com/v1/search/blog.json"

    def __init__(self, client_id: str, client_secret: str) -> None:
        headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
        self.http = HttpClient(headers=headers)

    def blog_total(self, keyword: str) -> Optional[int]:
        try:
            data = self.http.get_json(self.BASE_URL, params={"query": keyword, "display": 1})
            total = data.get("total") if isinstance(data, dict) else None
            return int(total) if isinstance(total, int) else None
        except Exception:
            return None


class GoogleCSEnricher:
    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str, cx: str) -> None:
        self.http = HttpClient()
        self.api_key = api_key
        self.cx = cx

    def total_results(self, keyword: str) -> Optional[int]:
        try:
            data = self.http.get_json(self.BASE_URL, params={"key": self.api_key, "cx": self.cx, "q": keyword})
            info = data.get("searchInformation") if isinstance(data, dict) else None
            total = info.get("totalResults") if isinstance(info, dict) else None
            return int(total) if isinstance(total, str) and total.isdigit() else None
        except Exception:
            return None


class NaverAdsEnricher:
    BASE_URL = "https://api.searchad.naver.com"

    def __init__(self, customer_id: string, api_key: string, secret_key: string) -> None:  # type: ignore[name-defined]
        self.customer_id = customer_id
        self.api_key = api_key
        self.secret_key = secret_key

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        ts = str(int(time.time() * 1000))
        sig = base64.b64encode(hmac.new(self.secret_key.encode(), f"{ts}.{method}.{path}".encode(), hashlib.sha256).digest()).decode()
        return {"X-Timestamp": ts, "X-API-KEY": self.api_key, "X-Customer": self.customer_id, "X-Signature": sig}

    def keyword_stats(self, keyword: str) -> tuple[Optional[int], Optional[int], Optional[float]]:
        path = "/keywordstool"
        url = f"{self.BASE_URL}{path}"
        try:
            http = HttpClient(headers=self._headers("GET", path))
            data = http.get_json(url, params={"hintKeywords": keyword, "showDetail": 1})
            lst = data.get("keywordList") if isinstance(data, dict) else None
            if isinstance(lst, list) and lst:
                it = lst[0]
                pc = it.get("monthlyPcQcCnt") if isinstance(it, dict) else None
                mob = it.get("monthlyMobileQcCnt") if isinstance(it, dict) else None
                cpc = it.get("plAvgCpc") if isinstance(it, dict) else None
                pc_i = int(pc) if isinstance(pc, (int, float, str)) and str(pc).isdigit() else None
                mob_i = int(mob) if isinstance(mob, (int, float, str)) and str(mob).isdigit() else None
                cpc_f = float(cpc) if isinstance(cpc, (int, float)) else None
                return pc_i, mob_i, cpc_f
        except Exception:
            return None, None, None
        return None, None, None


def build_enrichers_from_env() -> Dict[str, object]:
    enrichers: Dict[str, object] = {}
    cid = os.getenv("NAVER_AD_CUSTOMER_ID")
    k = os.getenv("NAVER_AD_API_KEY")
    s = os.getenv("NAVER_AD_SECRET_KEY")
    if cid and k and s:
        try:
            enrichers["naver_ads"] = NaverAdsEnricher(cid, k, s)  # type: ignore[arg-type]
        except Exception:
            pass

    nid = os.getenv("NAVER_OPENAPI_CLIENT_ID")
    nsec = os.getenv("NAVER_OPENAPI_CLIENT_SECRET")
    if nid and nsec:
        enrichers["naver_openapi"] = NaverOpenApiEnricher(nid, nsec)

    gk = os.getenv("GOOGLE_API_KEY")
    cx = os.getenv("GOOGLE_CSE_CX")
    if gk and cx:
        enrichers["google_cse"] = GoogleCSEnricher(gk, cx)
    return enrichers


def enrich_keywords(keywords: list[str], enrichers: Dict[str, object], limit: int | None = None) -> Dict[str, EnrichedMetrics]:
    out: Dict[str, EnrichedMetrics] = {}
    limit = limit or len(keywords)
    for kw in keywords[:limit]:
        m = EnrichedMetrics(keyword=kw)
        if "naver_openapi" in enrichers:
            m.naver_blog_total = enrichers["naver_openapi"].blog_total(kw)  # type: ignore[attr-defined]
        if "google_cse" in enrichers:
            m.google_total = enrichers["google_cse"].total_results(kw)  # type: ignore[attr-defined]
        if "naver_ads" in enrichers:
            pc, mob, cpc = enrichers["naver_ads"].keyword_stats(kw)  # type: ignore[attr-defined]
            m.naver_monthly_pc, m.naver_monthly_mobile, m.naver_cpc = pc, mob, cpc
        out[kw] = m
    return out

