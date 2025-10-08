from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
import streamlit as st

from .env import load_env
from .enrichers import build_enrichers_from_env
from .text_utils import normalize_query


def _to_csv_bytes(rows: List[dict]) -> bytes:
    import csv
    import io

    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _google_cse_search(api_key: str, cx: str, q: str, num: int = 10) -> List[Dict[str, Any]]:
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "cx": cx, "q": q, "num": max(1, min(num, 10))}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": _UA})
        r.raise_for_status()
        j = r.json()
        items = j.get("items") or []
        return [
            {"title": it.get("title"), "link": it.get("link"), "snippet": it.get("snippet")}
            for it in items
        ]
    except Exception:
        return []


def main() -> None:
    load_env()
    st.set_page_config(page_title="Naver Keyword Monetizer", layout="wide")
    st.title("Naver Keyword Monetizer (API 기반)")

    with st.sidebar:
        st.header("설정")
        seed = st.text_input("시드 키워드", value="봄 여행")
        max_items = st.slider("최대 추천 수(SearchAd)", 10, 1000, 200, 10)
        st.caption("필수: NAVER_AD_*, 선택: GOOGLE_*")
        run = st.button("실행")

    enrichers = build_enrichers_from_env()
    if "naver_ads" not in enrichers:
        st.error("NAVER_AD_* 키가 필요합니다. .env 또는 Secrets에 설정하세요.")
        return

    if not run:
        st.info("시드를 입력하고 [실행]을 눌러주세요.")
        return

    seed = normalize_query(seed)
    if not seed:
        st.warning("시드 키워드를 입력하세요.")
        return

    ads = enrichers["naver_ads"]  # type: ignore[index]
    with st.spinner("SearchAd 연관 키워드 수집 중..."):
        rel = []
        try:
            rel = ads.related_keywords(seed, show_detail=1, max_rows=int(max_items))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            st.error(f"SearchAd 연동 오류: {e}")
            return
        if not rel:
            st.info("연관 키워드를 찾지 못했습니다.")
            return

    def _ival(x):
        try:
            return int(float(str(x).replace(",", "")))
        except Exception:
            return 0

    money_rows: List[Dict[str, Any]] = []
    for it in rel:
        kw = str(it.get("relKeyword", "")).strip()
        pc = _ival(it.get("monthlyPcQcCnt"))
        mo = _ival(it.get("monthlyMobileQcCnt"))
        pc_clk = _ival(it.get("monthlyAvePcClkCnt"))
        mo_clk = _ival(it.get("monthlyAveMobileClkCnt"))
        cpc = float(it.get("plAvgCpc") or 0.0)
        revenue = int((pc_clk + mo_clk) * cpc)
        money_rows.append({
            "relKeyword": kw,
            "monthlyPcQcCnt": pc,
            "monthlyMobileQcCnt": mo,
            "monthlyAvePcClkCnt": pc_clk,
            "monthlyAveMobileClkCnt": mo_clk,
            "plAvgCpc": cpc,
            "예상_수익(원)": revenue,
        })

    tabs = st.tabs([
        "수익 분석(SearchAd)",
        "연관 키워드(SearchAd)",
        "검색 결과(Google CSE)",
    ])

    with tabs[0]:
        st.subheader("API 기반 지표 + 수익 추정")
        total_rev = sum(r["예상_수익(원)"] for r in money_rows)
        st.metric("표시 키워드 수", len(money_rows))
        st.metric("예상 수익 합(단순)", f"{total_rev:,}원")
        show_cols = [
            "relKeyword",
            "monthlyPcQcCnt",
            "monthlyMobileQcCnt",
            "monthlyAvePcClkCnt",
            "monthlyAveMobileClkCnt",
            "plAvgCpc",
            "예상_수익(원)",
        ]
        view = sorted(money_rows, key=lambda x: (x["예상_수익(원)"], x["plAvgCpc"]), reverse=True)
        st.dataframe([{k: r.get(k) for k in show_cols} for r in view], use_container_width=True)
        st.download_button(
            "CSV 다운로드(수익 분석)",
            data=_to_csv_bytes([{k: r.get(k) for k in show_cols} for r in view]),
            file_name="monetization.csv",
            mime="text/csv",
        )

    with tabs[1]:
        st.subheader("SearchAd 연관 키워드(원본 일부)")
        keep = [
            "relKeyword",
            "monthlyPcQcCnt",
            "monthlyMobileQcCnt",
            "monthlyAvePcClkCnt",
            "monthlyAveMobileClkCnt",
            "plAvgCpc",
            "compIdx",
        ]
        trimmed = [{k: it.get(k) for k in keep if k in it} for it in rel]
        st.dataframe(trimmed, use_container_width=True)

    with tabs[2]:
        st.subheader("Google CSE 결과")
        g_api = os.getenv("GOOGLE_API_KEY", "")
        g_cx = os.getenv("GOOGLE_CSE_CX", "")
        kw_g = st.text_input("검색 키워드", value=seed, key="g_kw")
        num_g = st.slider("검색 결과 수", 1, 10, 10, 1)
        if not g_api or not g_cx:
            st.info("GOOGLE_API_KEY / GOOGLE_CSE_CX가 필요합니다.")
        else:
            if st.button("CSE 조회"):
                items = _google_cse_search(g_api, g_cx, kw_g, num=num_g)
                st.dataframe(items or [], use_container_width=True)

