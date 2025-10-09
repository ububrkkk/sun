from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
import streamlit as st

from blog_keyword_analyzer.env import load_env
from blog_keyword_analyzer.enrichers import build_enrichers_from_env
from blog_keyword_analyzer.text_utils import normalize_query


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _ival(x: Any) -> int:
    try:
        return int(float(str(x).replace(",", "")))
    except Exception:
        return 0


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


def _google_cse_search(api_key: str, cx: str, q: str, num: int = 10) -> List[Dict[str, Any]]:
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "cx": cx, "q": q, "num": max(1, min(num, 10))}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": UA})
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
    st.title("네이버 키워드 수익 분석 (API)")

    with st.sidebar:
        st.header("설정")
        seed = st.text_input("시드 키워드", value="포항 맛집", key="seed_input")
        max_items = st.slider("최대 추천 수(SearchAd)", 10, 1000, 200, 10)
        st.subheader("필터")
        min_total_vol = st.number_input("최소 검색량 합(PC+MO)", 0, 1_000_000, 0, 50)
        min_clicks = st.number_input("최소 평균 클릭수 합", 0, 1_000_000, 0, 10)
        min_cpc = st.number_input("최소 CPC(원)", 0, 1_000_000, 0, 10)
        exclude_text = st.text_input(
            "제외 키워드(쉼표)",
            value="가격, 원, 비용, 최저가, 할인, 쿠폰, 무료, 유료, 시세, 견적",
        )
        sort_choice = st.selectbox(
            "정렬 기준",
            ("예상 수익", "클릭 합계", "검색량 합계", "경쟁 지수"),
            index=0,
        )
        descending = st.checkbox("내림차순 정렬", value=True)
        display_top = st.slider("표시 Top N", 10, 1000, 200, 10)
        st.caption("필수: NAVER_AD_*; 선택: GOOGLE_*")
        auto_run = st.checkbox("시드 변경 시 자동 실행", value=True)
        refresh = st.button("새로고침")
        run = st.button("실행")

    enrichers = build_enrichers_from_env()
    ok_ads = "naver_ads" in enrichers
    ok_cse = "google_cse" in enrichers
    cols = st.columns(2)
    cols[0].metric("SearchAd 키", "OK" if ok_ads else "없음")
    cols[1].metric("Google CSE 키", "OK" if ok_cse else "없음")
    if not ok_ads:
        st.error("NAVER_AD_* keys are required. Set them in Secrets or .env.")
        return

    seed = normalize_query(seed)
    if "seed_nonce" not in st.session_state:
        st.session_state["seed_nonce"] = 0
    if "last_seed" not in st.session_state:
        st.session_state["last_seed"] = seed
    if auto_run and seed != st.session_state["last_seed"]:
        st.session_state["seed_nonce"] += 1
        st.session_state["last_seed"] = seed
    if refresh:
        st.session_state["seed_nonce"] += 1

    if not run and not auto_run and not refresh:
        st.info("Enter a seed and click [Run].")
        return
    if not seed:
        st.warning("Please enter a seed keyword.")
        return

    ads = enrichers["naver_ads"]  # type: ignore[index]

    @st.cache_data(show_spinner=False, ttl=60)
    def _fetch_related_cached(seed_key: str, limit: int, nonce: int) -> List[Dict[str, Any]]:
        # 1) main query
        try:
            rows = ads.related_keywords(seed_key, show_detail=1, max_rows=int(limit))  # type: ignore[attr-defined]
        except Exception:
            rows = []
        if rows:
            return rows
        # 2) token queries
        toks = [t for t in seed_key.split(" ") if t]
        seen: Dict[str, Dict[str, Any]] = {}
        if len(toks) >= 2:
            for t in toks:
                try:
                    sub = ads.related_keywords(t, show_detail=1, max_rows=int(limit))  # type: ignore[attr-defined]
                except Exception:
                    sub = []
                for it in sub or []:
                    k = str(it.get("relKeyword", "")).strip()
                    if k and k not in seen:
                        seen[k] = it
            if seen:
                return list(seen.values())[: int(limit)]
            # 3) no-space query
            no_space = seed_key.replace(" ", "")
            try:
                rows2 = ads.related_keywords(no_space, show_detail=1, max_rows=int(limit))  # type: ignore[attr-defined]
                if rows2:
                    return rows2
            except Exception:
                pass
        return []

    with st.spinner("Fetching related keywords from SearchAd..."):
        try:
            rel = _fetch_related_cached(seed, int(max_items), int(st.session_state["seed_nonce"]))
        except Exception as e:  # noqa: BLE001
            st.error(f"SearchAd error: {e}")
            st.stop()
        if not isinstance(rel, list):
            st.error("Invalid SearchAd response.")
            st.stop()
        if not rel:
            # Fallback with local modifiers
            local_mods = [
                "맛집", "카페", "브런치", "회", "해산물", "시장", "야시장",
                "데이트", "가성비", "예약", "주차", "24시",
            ]
            seeds = []
            base = seed.strip()
            if "맛집" not in base:
                seeds.append(f"{base} 맛집")
            seeds.append(base)
            for m in local_mods:
                seeds.append(f"{base} {m}")
            seen2: Dict[str, Dict[str, Any]] = {}
            for q in seeds[:25]:
                try:
                    sub = ads.related_keywords(q, show_detail=1, max_rows=int(max_items))  # type: ignore[attr-defined]
                except Exception:
                    sub = []
                for it in sub or []:
                    rk = str(it.get("relKeyword", "")).strip()
                    if rk and rk not in seen2:
                        seen2[rk] = it
            rel = list(seen2.values())
            if not rel:
                st.info("API에서 연관 키워드를 찾지 못했습니다. 더 단순한 시드를 사용해보세요.")
                st.stop()

    # Build rows
    money_rows: List[Dict[str, Any]] = []
    for it in rel:
        kw = str(it.get("relKeyword", "")).strip()
        pc = _ival(it.get("monthlyPcQcCnt"))
        mo = _ival(it.get("monthlyMobileQcCnt"))
        pc_clk = _ival(it.get("monthlyAvePcClkCnt"))
        mo_clk = _ival(it.get("monthlyAveMobileClkCnt"))
        cpc = float(it.get("plAvgCpc") or 0.0)
        revenue = int((pc_clk + mo_clk) * cpc)
        money_rows.append(
            {
                "relKeyword": kw,
                "monthlyPcQcCnt": pc,
                "monthlyMobileQcCnt": mo,
                "monthlyAvePcClkCnt": pc_clk,
                "monthlyAveMobileClkCnt": mo_clk,
                "plAvgCpc": cpc,
                "compIdx": it.get("compIdx"),
                "est_revenue": revenue,
                "sum_volume": pc + mo,
                "sum_clicks": pc_clk + mo_clk,
            }
        )

    # 가격/비용 관련 키워드 제외
    exclude_set = {t.strip() for t in exclude_text.split(',') if t.strip()}
    if exclude_set:
        money_rows = [r for r in money_rows if not any(tok in r.get("relKeyword", "") for tok in exclude_set)]

    # Preserve original for Top tab
    orig_rows = list(money_rows)

    # Apply filters
    filtered = [
        r
        for r in money_rows
        if r.get("sum_volume", 0) >= int(min_total_vol)
        and r.get("sum_clicks", 0) >= int(min_clicks)
        and float(r.get("plAvgCpc", 0.0)) >= float(min_cpc)
    ]
    money_rows = filtered or money_rows

    tabs = st.tabs(["수익 분석", "연관 키워드", "Google CSE", "인기 검색"]) 

    with tabs[0]:
        st.subheader("API 지표 + 수익 추정")
        total_rev = sum(int(r.get("est_revenue", 0)) for r in money_rows)
        st.metric("표시 행 수", len(money_rows))
        st.metric("예상 수익 합계", f"{total_rev:,}원")
        show_cols = [
            "relKeyword",
            "sum_volume",
            "monthlyPcQcCnt",
            "monthlyMobileQcCnt",
            "sum_clicks",
            "monthlyAvePcClkCnt",
            "monthlyAveMobileClkCnt",
            "est_revenue",
        ]

        sort_map = {
            "예상 수익": "est_revenue",
            "클릭 합계": "sum_clicks",
            "검색량 합계": "sum_volume",
            "경쟁 지수": "compIdx",
        }
        sort_key = sort_map.get(sort_choice, "est_revenue")

        def _sort_key(row: Dict[str, Any]):
            primary = row.get(sort_key, 0)
            secondary = row.get("sum_clicks", 0)
            return (primary, secondary)

        view = sorted(money_rows, key=_sort_key, reverse=bool(descending))[: int(display_top)]
        st.dataframe([{k: r.get(k) for k in show_cols} for r in view], use_container_width=True)
        st.download_button(
            "CSV 다운로드(수익 분석)",
            data=_to_csv_bytes([{k: r.get(k) for k in show_cols} for r in view]),
            file_name="monetization.csv",
            mime="text/csv",
        )
        with st.expander("디버그(첫 행)"):
            if money_rows:
                st.json(money_rows[0])

    with tabs[1]:
        st.subheader("SearchAd 연관 키워드(일부)")
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
        page_size = st.slider("페이지 크기", 10, 200, 50, 10)
        page = st.number_input("페이지", min_value=1, value=1, step=1)
        start = (int(page) - 1) * int(page_size)
        end = start + int(page_size)
        st.dataframe(trimmed[start:end], use_container_width=True)

    with tabs[2]:
        st.subheader("Google CSE 결과")
        g_api = os.getenv("GOOGLE_API_KEY", "")
        g_cx = os.getenv("GOOGLE_CSE_CX", "")
        kw_g = st.text_input("검색어", value=seed, key="g_kw")
        num_g = st.slider("결과 수", 1, 10, 10, 1)
        if not g_api or not g_cx:
            st.info("GOOGLE_API_KEY / GOOGLE_CSE_CX가 필요합니다.")
        else:
            if st.button("CSE 조회"):
                items = _google_cse_search(g_api, g_cx, kw_g, num=num_g)
                st.dataframe(items or [], use_container_width=True)

    with tabs[3]:
        st.subheader("인기 검색 (API 기반)")
        rows_tot = list(orig_rows)
        top_vol = sorted(rows_tot, key=lambda x: x.get("sum_volume", 0), reverse=True)[:20]
        top_clk = sorted(rows_tot, key=lambda x: x.get("sum_clicks", 0), reverse=True)[:20]
        st.markdown("**검색량 Top 20 (PC+MO)**")
        st.dataframe([
            {k: r.get(k) for k in ["relKeyword", "sum_volume", "monthlyPcQcCnt", "monthlyMobileQcCnt"]}
            for r in top_vol
        ], use_container_width=True)
        st.markdown("**클릭수 Top 20 (PC+MO)**")
        st.dataframe([
            {k: r.get(k) for k in ["relKeyword", "sum_clicks", "monthlyAvePcClkCnt", "monthlyAveMobileClkCnt"]}
            for r in top_clk
        ], use_container_width=True)
