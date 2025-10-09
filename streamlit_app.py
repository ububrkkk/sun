"""Streamlit entrypoint (repo root).

Runs the app from `blog_keyword_analyzer.streamlit_api_only:main`.
If imports fail for any reason, falls back to a self‑contained
inline implementation so this file always works as the main entrypoint.
"""

from __future__ import annotations

import os
import sys
import types
import importlib.util
from typing import Callable
import time
import hmac
import hashlib
import base64
import requests
import streamlit as st
import io
import csv


def _src_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "src")


def _ensure_src_on_path() -> None:
    src = _src_dir()
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


def _load_app_main_via_spec() -> Callable[[], int | None]:
    src = _src_dir()
    pkg_name = "blog_keyword_analyzer"
    mod_name = f"{pkg_name}.streamlit_api_only"
    pkg_path = os.path.join(src, pkg_name)
    file_path = os.path.join(pkg_path, "streamlit_api_only.py")

    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [pkg_path]  # type: ignore[attr-defined]
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("Cannot load app spec")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg_name
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "main"):
        raise RuntimeError("Entrypoint main() not found")
    return getattr(module, "main")


_ensure_src_on_path()


# ---------- Inline fallback implementation (used only if imports fail) ----------

def _env_get(name: str) -> str:
    try:
        return (getattr(st, "secrets", {}).get(name, "") or os.getenv(name, "")).strip()
    except Exception:
        return os.getenv(name, "").strip()


def _sign_headers(customer_id: str, api_key: str, secret_key: str, method: str, path: str) -> dict:
    ts = str(int(time.time() * 1000))
    sig = base64.b64encode(
        hmac.new(secret_key.encode(), f"{ts}.{method}.{path}".encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "X-Timestamp": ts,
        "X-API-KEY": api_key,
        "X-Customer": customer_id,
        "X-Signature": sig,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=UTF-8",
    }


@st.cache_data(show_spinner=False, ttl=60)
def _ads_related(seed: str, limit: int, customer_id: str, api_key: str, secret_key: str) -> list[dict]:
    url = "https://api.searchad.naver.com/keywordstool"
    headers = _sign_headers(customer_id, api_key, secret_key, "GET", "/keywordstool")
    params = {"hintKeywords": seed, "showDetail": 1, "includeHintKeywords": 1}
    r = requests.get(url, headers=headers, params=params, timeout=8)
    r.raise_for_status()
    j = r.json() if r.content else {}
    lst = j.get("keywordList") if isinstance(j, dict) else None
    rows = [x for x in (lst or []) if isinstance(x, dict)]
    return rows[: int(limit)]


def _ival(x) -> int:
    try:
        return int(float(str(x).replace(",", "")))
    except Exception:
        return 0


def _to_csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


def _cse_search(api_key: str, cx: str, q: str, num: int = 10) -> list[dict]:
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "cx": cx, "q": q, "num": max(1, min(int(num), 10))}
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        j = r.json() if r.content else {}
        items = j.get("items") or []
        return [
            {"title": it.get("title"), "link": it.get("link"), "snippet": it.get("snippet")}
            for it in items
        ]
    except Exception:
        return []


def _inline_main() -> None:
    st.set_page_config(page_title="Naver Keyword Monetizer", layout="wide")
    st.title("Naver Keyword Monetizer (API 기반)")

    with st.sidebar:
        st.header("설정")
        seed = st.text_input("시드 키워드", value="봄 여행")
        max_items = st.slider("최대 추천 수(SearchAd)", 10, 1000, 200, 10)
        st.subheader("필터")
        min_total_vol = st.number_input("최소 검색량(PC+MO)", 0, 1_000_000, 0, 50)
        min_clicks = st.number_input("최소 평균 클릭수(합)", 0, 1_000_000, 0, 10)
        min_cpc = st.number_input("최소 CPC(원)", 0, 1_000_000, 0, 10)
        sort_by = st.selectbox(
            "정렬 기준",
            (
                "예상_수익(원)",
                "plAvgCpc",
                "monthlyAvePcClkCnt+monthlyAveMobileClkCnt",
                "monthlyPcQcCnt+monthlyMobileQcCnt",
                "compIdx",
            ),
            index=0,
        )
        descending = st.checkbox("내림차순 정렬", value=True)
        display_top = st.slider("표시 Top N", 10, 1000, 200, 10)
        run = st.button("실행")

    cid = _env_get("NAVER_AD_CUSTOMER_ID")
    k = _env_get("NAVER_AD_API_KEY")
    s = _env_get("NAVER_AD_SECRET_KEY")
    gk = _env_get("GOOGLE_API_KEY")
    gcx = _env_get("GOOGLE_CSE_CX")

    cols = st.columns(2)
    cols[0].metric("SearchAd 키", "OK" if (cid and k and s) else "없음")
    cols[1].metric("Google CSE 키", "OK" if (gk and gcx) else "없음")
    if not (cid and k and s):
        st.error("NAVER_AD_* 키가 필요합니다. Secrets 또는 .env에 설정하세요.")
        return

    if not run:
        st.info("시드를 입력하고 [실행]을 눌러주세요.")
        return

    seed = (seed or "").strip()
    if not seed:
        st.warning("시드 키워드를 입력하세요.")
        return

    with st.spinner("SearchAd 연관 키워드 수집 중..."):
        try:
            rel = _ads_related(seed, int(max_items), cid, k, s)
        except Exception as e:
            st.error(f"SearchAd 연동 오류: {e}")
            return
        if not rel:
            # 간단 폴백: 공백으로 분할 재시도
            toks = [t for t in seed.split(" ") if t]
            seen = {}
            for t in toks:
                try:
                    sub = _ads_related(t, int(max_items), cid, k, s)
                except Exception:
                    sub = []
                for it in sub or []:
                    rk = str(it.get("relKeyword", "")).strip()
                    if rk and rk not in seen:
                        seen[rk] = it
            rel = list(seen.values())
            if not rel and len(toks) >= 2:
                no_space = seed.replace(" ", "")
                try:
                    rel = _ads_related(no_space, int(max_items), cid, k, s)
                except Exception:
                    rel = []
        if not rel:
            st.info("연관 키워드를 찾지 못했습니다. 시드를 더 일반적인 단어로 바꾸거나 공백을 줄여보세요.")
            return

    money_rows: list[dict] = []
    for it in rel:
        kw = str(it.get("relKeyword", "")).strip()
        pc = _ival(it.get("monthlyPcQcCnt"))
        mo = _ival(it.get("monthlyMobileQcCnt"))
        pc_clk = _ival(it.get("monthlyAvePcClkCnt"))
        mo_clk = _ival(it.get("monthlyAveMobileClkCnt"))
        cpc = float(it.get("plAvgCpc") or 0.0)
        revenue = int((pc_clk + mo_clk) * cpc)
        row = {
            "relKeyword": kw,
            "monthlyPcQcCnt": pc,
            "monthlyMobileQcCnt": mo,
            "monthlyAvePcClkCnt": pc_clk,
            "monthlyAveMobileClkCnt": mo_clk,
            "plAvgCpc": cpc,
            "compIdx": it.get("compIdx"),
            "예상_수익(원)": revenue,
        }
        money_rows.append(row)

    def _tot_vol(r: dict) -> int:
        return int(r.get("monthlyPcQcCnt", 0)) + int(r.get("monthlyMobileQcCnt", 0))

    def _tot_clk(r: dict) -> int:
        return int(r.get("monthlyAvePcClkCnt", 0)) + int(r.get("monthlyAveMobileClkCnt", 0))

    money_rows = [
        r
        for r in money_rows
        if _tot_vol(r) >= int(min_total_vol)
        and _tot_clk(r) >= int(min_clicks)
        and float(r.get("plAvgCpc", 0.0)) >= float(min_cpc)
    ]

    tabs = st.tabs(["수익 분석(SearchAd)", "연관 키워드(SearchAd)", "검색 결과(Google CSE)"])

    with tabs[0]:
        st.subheader("API 기반 지표 + 수익 추정")
        total_rev = sum(int(r.get("예상_수익(원)", 0)) for r in money_rows)
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

        def _sort_key(row: dict):
            if sort_by == "예상_수익(원)":
                return (row.get("예상_수익(원)", 0), row.get("plAvgCpc", 0))
            if sort_by == "plAvgCpc":
                return (row.get("plAvgCpc", 0), row.get("예상_수익(원)", 0))
            if sort_by == "monthlyAvePcClkCnt+monthlyAveMobileClkCnt":
                return (_tot_clk(row), row.get("예상_수익(원)", 0))
            if sort_by == "monthlyPcQcCnt+monthlyMobileQcCnt":
                return (_tot_vol(row), row.get("예상_수익(원)", 0))
            if sort_by == "compIdx":
                return (float(row.get("compIdx", 0.0) or 0.0), row.get("예상_수익(원)", 0))
            return (row.get("예상_수익(원)", 0), row.get("plAvgCpc", 0))

        view = sorted(money_rows, key=_sort_key, reverse=True if descending else False)[: int(display_top)]
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
        page_size = st.slider("페이지 크기", 10, 200, 50, 10)
        page = st.number_input("페이지", min_value=1, value=1, step=1)
        start = (int(page) - 1) * int(page_size)
        end = start + int(page_size)
        st.dataframe(trimmed[start:end], use_container_width=True)

    with tabs[2]:
        st.subheader("Google CSE 결과")
        kw_g = st.text_input("검색 키워드", value=seed, key="g_kw")
        num_g = st.slider("검색 결과 수", 1, 10, 10, 1)
        if not (gk and gcx):
            st.info("GOOGLE_API_KEY / GOOGLE_CSE_CX가 필요합니다.")
        else:
            if st.button("CSE 조회"):
                items = _cse_search(gk, gcx, kw_g, num=int(num_g))
                st.dataframe(items or [], use_container_width=True)

try:
    from blog_keyword_analyzer.streamlit_api_only import main  # type: ignore  # noqa: E402
except Exception:
    try:
        main = _load_app_main_via_spec()
    except Exception:
        main = _inline_main

if __name__ == "__main__":
    # Streamlit executes this file as a script; calling main starts the app
    main()
