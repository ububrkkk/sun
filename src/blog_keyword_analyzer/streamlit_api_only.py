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
    st.title("Naver Keyword Monetizer (API)")

    with st.sidebar:
        st.header("Settings")
        seed = st.text_input("Seed keyword", value="포항 맛집", key="seed_input")
        max_items = st.slider("Max suggestions (SearchAd)", 10, 1000, 200, 10)
        st.subheader("Filters")
        min_total_vol = st.number_input("Min volume (PC+MO)", 0, 1_000_000, 0, 50)
        min_clicks = st.number_input("Min avg clicks (sum)", 0, 1_000_000, 0, 10)
        min_cpc = st.number_input("Min CPC (KRW)", 0, 1_000_000, 0, 10)
        sort_by = st.selectbox(
            "Sort by",
            ("est_revenue", "plAvgCpc", "sum_clicks", "sum_volume", "compIdx"),
            index=0,
        )
        descending = st.checkbox("Sort descending", value=True)
        display_top = st.slider("Display Top N", 10, 1000, 200, 10)
        st.caption("Required: NAVER_AD_*; Optional: GOOGLE_*")
        auto_run = st.checkbox("Auto-run on seed change", value=True)
        refresh = st.button("Refresh")
        run = st.button("Run")

    enrichers = build_enrichers_from_env()
    ok_ads = "naver_ads" in enrichers
    ok_cse = "google_cse" in enrichers
    cols = st.columns(2)
    cols[0].metric("SearchAd Key", "OK" if ok_ads else "Missing")
    cols[1].metric("Google CSE Key", "OK" if ok_cse else "Missing")
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
                st.info("No related keywords from API. Try a simpler seed.")
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

    tabs = st.tabs(["Monetization", "Related (API)", "Google CSE", "Top Searches"]) 

    with tabs[0]:
        st.subheader("API-based metrics + revenue estimate")
        total_rev = sum(int(r.get("est_revenue", 0)) for r in money_rows)
        st.metric("Rows", len(money_rows))
        st.metric("Sum(est revenue)", f"{total_rev:,} KRW")
        show_cols = [
            "relKeyword",
            "sum_volume",
            "monthlyPcQcCnt",
            "monthlyMobileQcCnt",
            "sum_clicks",
            "monthlyAvePcClkCnt",
            "monthlyAveMobileClkCnt",
            "plAvgCpc",
            "est_revenue",
        ]

        def _sort_key(row: Dict[str, Any]):
            if sort_by == "est_revenue":
                return (row.get("est_revenue", 0), row.get("plAvgCpc", 0))
            if sort_by == "plAvgCpc":
                return (row.get("plAvgCpc", 0), row.get("est_revenue", 0))
            if sort_by == "sum_clicks":
                return (row.get("sum_clicks", 0), row.get("est_revenue", 0))
            if sort_by == "sum_volume":
                return (row.get("sum_volume", 0), row.get("est_revenue", 0))
            if sort_by == "compIdx":
                return (float(row.get("compIdx", 0.0) or 0.0), row.get("est_revenue", 0))
            return (row.get("est_revenue", 0), row.get("plAvgCpc", 0))

        view = sorted(money_rows, key=_sort_key, reverse=bool(descending))[: int(display_top)]
        st.dataframe([{k: r.get(k) for k in show_cols} for r in view], use_container_width=True)
        st.download_button(
            "Download CSV (Monetization)",
            data=_to_csv_bytes([{k: r.get(k) for k in show_cols} for r in view]),
            file_name="monetization.csv",
            mime="text/csv",
        )
        with st.expander("Debug (first row)"):
            if money_rows:
                st.json(money_rows[0])

    with tabs[1]:
        st.subheader("SearchAd related keywords (trimmed)")
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
        page_size = st.slider("Page size", 10, 200, 50, 10)
        page = st.number_input("Page", min_value=1, value=1, step=1)
        start = (int(page) - 1) * int(page_size)
        end = start + int(page_size)
        st.dataframe(trimmed[start:end], use_container_width=True)

    with tabs[2]:
        st.subheader("Google CSE results")
        g_api = os.getenv("GOOGLE_API_KEY", "")
        g_cx = os.getenv("GOOGLE_CSE_CX", "")
        kw_g = st.text_input("Query", value=seed, key="g_kw")
        num_g = st.slider("Results", 1, 10, 10, 1)
        if not g_api or not g_cx:
            st.info("Requires GOOGLE_API_KEY / GOOGLE_CSE_CX")
        else:
            if st.button("Search (CSE)"):
                items = _google_cse_search(g_api, g_cx, kw_g, num=num_g)
                st.dataframe(items or [], use_container_width=True)

    with tabs[3]:
        st.subheader("Top searches (from API)")
        rows_tot = list(orig_rows)
        top_vol = sorted(rows_tot, key=lambda x: x.get("sum_volume", 0), reverse=True)[:20]
        top_clk = sorted(rows_tot, key=lambda x: x.get("sum_clicks", 0), reverse=True)[:20]
        st.markdown("**Top 20 by volume (PC+MO)**")
        st.dataframe([
            {k: r.get(k) for k in ["relKeyword", "sum_volume", "monthlyPcQcCnt", "monthlyMobileQcCnt", "plAvgCpc"]}
            for r in top_vol
        ], use_container_width=True)
        st.markdown("**Top 20 by clicks (PC+MO)**")
        st.dataframe([
            {k: r.get(k) for k in ["relKeyword", "sum_clicks", "monthlyAvePcClkCnt", "monthlyAveMobileClkCnt", "plAvgCpc"]}
            for r in top_clk
        ], use_container_width=True)

