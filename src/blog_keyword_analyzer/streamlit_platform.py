from __future__ import annotations

import csv
import io
import os
import sys
from typing import Dict, List, Tuple
import datetime as dt
from typing import Any, Optional

import streamlit as st
import requests
from bs4 import BeautifulSoup

# Ensure parent 'src' is on sys.path when this file runs as app
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), 'src'))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from blog_keyword_analyzer.env import load_env
from blog_keyword_analyzer.expansion import expand_with_profile, expand_with_suffixes
from blog_keyword_analyzer.outline import build_outline
from blog_keyword_analyzer.providers import GoogleSuggestProvider, NaverSuggestProvider
from blog_keyword_analyzer.scoring import (
    KeywordScore,
    score_keywords,
    score_keywords_by_platform,
)
from blog_keyword_analyzer.text_utils import normalize_query, unique_ordered, tokenize
from blog_keyword_analyzer.enrichers import (
    build_enrichers_from_env,
    enrich_keywords,
    EnrichedMetrics,
)
from blog_keyword_analyzer.trends import compute_trends, default_hot_terms


@st.cache_data(show_spinner=False, ttl=30)
def collect_suggestions_cached(
    seeds: List[str], provider_names: List[str], depth: int, hl: str, nonce: int = 0
) -> Tuple[List[str], Dict[str, int]]:
    provider_names = [p.strip().lower() for p in provider_names]
    providers = []
    if "naver" in provider_names:
        providers.append(NaverSuggestProvider())
    if "google" in provider_names:
        providers.append(GoogleSuggestProvider())

    all_candidates: List[str] = []
    hit_counts: Dict[str, int] = {}

    def _accumulate(cands: List[str]) -> None:
        for kw in cands:
            all_candidates.append(kw)
            hit_counts[kw] = hit_counts.get(kw, 0) + 1

    for p in providers:
        if isinstance(p, GoogleSuggestProvider):
            _accumulate(p.bulk_suggest(seeds, hl=hl))
        else:
            _accumulate(p.bulk_suggest(seeds))

    if depth >= 2:
        suffix_expanded = expand_with_suffixes(seeds)
        for p in providers:
            if isinstance(p, GoogleSuggestProvider):
                _accumulate(p.bulk_suggest(suffix_expanded, hl=hl))
            else:
                _accumulate(p.bulk_suggest(suffix_expanded))

    return unique_ordered(all_candidates), hit_counts


def to_rows(scores: List[KeywordScore], metrics: Dict[str, EnrichedMetrics] | None) -> List[dict]:
    data: List[dict] = []
    for r in scores:
        data.append(
            {
                "keyword": r.keyword,
                "opportunity": r.opportunity,
                "demand": r.demand,
                "competition": r.competition,
                "provider_hits": r.provider_hits,
                **(
                    {}
                    if metrics is None
                    else {
                        "naver_blog_total": getattr(metrics.get(r.keyword), "naver_blog_total", None)
                        if metrics.get(r.keyword)
                        else None,
                        "google_total": getattr(metrics.get(r.keyword), "google_total", None)
                        if metrics.get(r.keyword)
                        else None,
                        "naver_monthly_pc": getattr(metrics.get(r.keyword), "naver_monthly_pc", None)
                        if metrics.get(r.keyword)
                        else None,
                        "naver_monthly_mobile": getattr(metrics.get(r.keyword), "naver_monthly_mobile", None)
                        if metrics.get(r.keyword)
                        else None,
                        "naver_cpc": getattr(metrics.get(r.keyword), "naver_cpc", None)
                        if metrics.get(r.keyword)
                        else None,
                    }
                ),
            }
        )
    return data


def to_csv_bytes(rows: List[dict]) -> bytes:
    buf = io.StringIO()
    if not rows:
        return b""
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


# ---- Extra helpers ported/simplified from external app ----

def _get_env_keys() -> Dict[str, str]:
    """Load env + Streamlit secrets into a handy dict."""
    load_env()

    def _getv(name: str) -> str:
        try:
            return (getattr(st, "secrets", {}).get(name, "") or os.getenv(name, "")).strip()
        except Exception:
            return os.getenv(name, "").strip()

    keys = {
        "NAVER_AD_API_KEY": _getv("NAVER_AD_API_KEY"),
        "NAVER_AD_SECRET_KEY": _getv("NAVER_AD_SECRET_KEY"),
        "NAVER_AD_CUSTOMER_ID": _getv("NAVER_AD_CUSTOMER_ID"),
        "NAVER_OPENAPI_CLIENT_ID": _getv("NAVER_OPENAPI_CLIENT_ID"),
        "NAVER_OPENAPI_CLIENT_SECRET": _getv("NAVER_OPENAPI_CLIENT_SECRET"),
        "GOOGLE_API_KEY": _getv("GOOGLE_API_KEY"),
        "GOOGLE_CSE_CX": _getv("GOOGLE_CSE_CX"),
        "KAKAO_REST_API_KEY": _getv("KAKAO_REST_API_KEY"),
    }
    return keys


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_naver_suggestions_raw(query: str, st_code: str = "100") -> List[str]:
    try:
        url = "https://ac.search.naver.com/nx/ac"
        params = {
            "q": query,
            "st": st_code,
            "r_format": "json",
            "r_enc": "utf-8",
            "frm": "nv",
            "ans": "2",
            "r_lt": "1",
        }
        r = requests.get(url, params=params, timeout=6, headers={"User-Agent": _UA})
        r.raise_for_status()
        j = r.json()
        out: List[str] = []
        items = (j.get("items") or [])
        if items and isinstance(items, list):
            for entry in items[0]:
                if isinstance(entry, list) and entry:
                    s = str(entry[0]).strip()
                    if s:
                        out.append(s)
        return out
    except Exception:
        return []


def google_cse_search(api_key: str, cx: str, q: str, num: int = 10) -> List[Dict[str, Any]]:
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


def kakao_blog_search(api_key: str, query: str, size: int = 10, page: int = 1, sort: str = "recency") -> List[Dict[str, Any]]:
    try:
        url = "https://dapi.kakao.com/v2/search/blog"
        params = {"query": query, "size": max(1, min(size, 50)), "page": max(page, 1), "sort": sort}
        headers = {"Authorization": f"KakaoAK {api_key}", "User-Agent": _UA}
        r = requests.get(url, params=params, headers=headers, timeout=8)
        r.raise_for_status()
        j = r.json()
        docs = j.get("documents")
        return docs if isinstance(docs, list) else []
    except Exception:
        return []


def fetch_url_html(url: str, timeout: int = 10) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
        r.raise_for_status()
        return r.text
    except Exception:
        return ""


def analyze_html_structure(html: str) -> Dict[str, int]:
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        return {"words": 0, "h2": 0, "h3": 0, "img": 0, "table": 0}
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = (soup.get_text(separator=" ") or "").strip()
    words = len([w for w in text.split() if w])
    h2 = len(soup.find_all("h2"))
    h3 = len(soup.find_all("h3"))
    img = len(soup.find_all("img"))
    table = len(soup.find_all("table"))
    return {"words": words, "h2": h2, "h3": h3, "img": img, "table": table}


def beginner_article_type(intent: str) -> str:
    if intent in ("비교", "리뷰"):
        return "가이드/비교/하이라이트"
    if intent == "정보":
        return "소개/개요/사용 가이드"
    return "초보자 가이드/체크리스트"


def basic_ad_positions(platform: str) -> str:
    if platform.lower().startswith("tistory"):
        return "상단 1 + 본문 중간 1 + 하단 1"
    return "상단 이미지 하단 1 + 본문 중간 1 + 사이드 1"


def make_sponsor_pitch(brand: str, keyword: str, platform: str) -> str:
    brand = (brand or "스폰서").strip()
    platform = (platform or "블로그").strip()
    return (
        f"안녕하세요, {brand} 담당자님.\n\n"
        f"'{keyword}' 키워드로 {platform}를 운영하며, 타깃 독자에게 도달합니다.\n"
        f"귀사의 제품/서비스와 주제의 적합도가 높아 협업을 제안드립니다.\n\n"
        "제안 내용: 체크리스트/가이드형 콘텐츠 + CTA 배치(상/중/하)\n"
        "구성: 리뷰/비교 + 추가 CTA, 내부/외부 링크\n"
        "일정: 초안 3일, 피드백 반영 7일 내 게시\n\n"
        "검토 부탁드립니다. 감사합니다."
    )


def unique_title_meta_for_row(kw: str, intent: str, vol_pc: int, vol_mo: int, rank: int) -> Dict[str, str]:
    base = kw.strip()
    total = (vol_pc or 0) + (vol_mo or 0)
    year = dt.date.today().year
    if intent in ("비교", "리뷰"):
        patterns = [
            f"{base} {year} 구매 가이드 | 핵심 스펙 비교",
            f"{base} 필수 체크리스트 12가지",
            f"{base} TOP7 추천 | 장단점 요약",
            f"{base} 입문자 실수 모음 | 회피 팁",
            f"{base} 신제품 vs 가성비 | 무엇이 다를까",
        ]
        metas = [
            f"{base} 선택 전 꼭 보는 체크리스트와 스펙 비교, 예산/환경별 추천.",
            f"{base} 주요 포인트를 쉽게 정리했습니다. A/S와 유지비 팁 포함.",
        ]
    elif intent == "정보":
        patterns = [
            f"{base} 첫걸음 사용법 10분 요약",
            f"{base} 기초부터 핵심까지 | 시간 절약 가이드",
            f"{base} 문제 해결 Q&A 20선",
        ]
        metas = [
            f"{base} 초보도 바로 따라하는 개요·설정·활용법. 체크포인트 정리.",
        ]
    else:
        patterns = [
            f"{base} 실전 가이드 | 실수 막는 꿀팁",
            f"{base} 추천 리스트 | 꼭 알아야 할 선택 요령",
            f"{base} 비교/대안 총정리 15가지",
            f"{base} Q&A 20문20답 | 쉽게 정리",
        ]
        metas = [
            f"{base} 첫 구매 전 알아둘 점을 정리. 비교, 체크리스트, FAQ 포함.",
        ]
    if total >= 20000:
        patterns = [p.replace("가이드", "최신 가이드").replace("추천", "베스트 추천") for p in patterns]
    title = patterns[rank % len(patterns)]
    meta = metas[rank % len(metas)]
    if len(title) > 38:
        title = title[:36] + "…"
    if len(meta) > 110:
        meta = meta[:108] + "…"
    return {"title": title, "meta": meta}


def _simple_intent(kw: str) -> str:
    toks = tokenize(kw)
    for t in toks:
        if t in ("비교", "vs", "리뷰", "후기"):
            return "비교"
        if t in ("방법", "설명", "정보", "가이드"):
            return "정보"
    return "일반"


def _to_int(x: Any) -> int:
    try:
        s = str(x)
        if "<" in s:
            return 0
        return int(float(s.replace(",", "")))
    except Exception:
        return 0


def main() -> None:
    load_env()
    st.set_page_config(page_title="Blog Keyword Analyzer", layout="wide")
    st.title("Blog Keyword Analyzer (Naver/Tistory)")

    with st.sidebar:
        st.header("Settings")
        providers = st.multiselect("Providers", ["naver", "google"], default=["naver", "google"])
        depth = st.slider("Depth", 1, 2, 2)
        profile = st.selectbox("Profile", ["", "travel", "food"], index=0)
        include_suffix = st.checkbox("Include long-tail suffixes", value=False)
        limit = st.number_input("Max candidates", min_value=50, max_value=2000, value=400, step=50)
        top = st.number_input("Top preview", min_value=10, max_value=300, value=80, step=10)
        enrich_limit = st.number_input("Enrich limit (API)", min_value=50, max_value=1000, value=200, step=50)
        platforms = st.multiselect("Platforms", ["naver", "tistory"], default=["naver", "tistory"])
        st.divider()
        st.caption("Real-time trend (suggest delta)")
        refresh = st.button("Refresh")

    seeds_text = st.text_area("Seed keywords (one per line)", "부산 맛집\n해운대 맛집")
    run = st.button("Run")

    if run:
        seeds = [normalize_query(s) for s in seeds_text.splitlines() if normalize_query(s)]
        if not seeds:
            st.warning("Enter at least one seed keyword.")
            return
        providers_use = providers or ["google"]

        if "nonce" not in st.session_state:
            st.session_state["nonce"] = 0
        if refresh:
            st.session_state["nonce"] += 1

        with st.spinner("Collecting suggestions..."):
            try:
                candidates, hit_counts = collect_suggestions_cached(
                    seeds, providers_use, depth=depth, hl="ko", nonce=st.session_state["nonce"]
                )
            except Exception as e:  # noqa: BLE001
                st.error(f"Suggest collection error: {e}")
                return

        if profile:
            candidates = unique_ordered(candidates + expand_with_profile(seeds, profile))
        elif include_suffix:
            candidates = unique_ordered(candidates + expand_with_suffixes(seeds))
        if limit:
            candidates = candidates[: int(limit)]

        st.info(f"Scoring {len(candidates)} candidates (API only)...")
        enrichers = build_enrichers_from_env()
        if not enrichers:
            st.error("API keys not found. Please set NAVER_*/GOOGLE_* in .env or Streamlit Secrets.")
            st.stop()
        # Show which APIs are active
        active = ", ".join(sorted(enrichers.keys())) or "(none)"
        st.success(f"Active APIs: {active}")
        metrics_map: Dict[str, EnrichedMetrics] = enrich_keywords(candidates, enrichers, limit=int(enrich_limit))

        if not platforms:
            platforms = ["naver", "tistory"]
        scored: Dict[str, List[KeywordScore]] = {}
        for pf in platforms:
            scored[pf] = score_keywords_by_platform(
                candidates, hit_counts=hit_counts, metrics=metrics_map, platform=pf
            )

        tabs = st.tabs([pf.upper() for pf in platforms])
        for i, pf in enumerate(platforms):
            with tabs[i]:
                rows = to_rows(scored[pf], metrics_map)
                st.dataframe(rows[: int(top)], use_container_width=True)
                csv_bytes = to_csv_bytes(rows)
                st.download_button(
                    f"Download CSV ({pf})",
                    data=csv_bytes,
                    file_name=f"results.{pf}.csv",
                    mime="text/csv",
                )
                if rows:
                    sel_kw = rows[0]["keyword"]
                    with st.expander(f"Outline preview: {sel_kw}"):
                        outline = build_outline(sel_kw)
                        st.write("Title:", outline["title"][0])
                        st.write("Sections:")
                        for s in outline["sections"]:
                            st.write("- ", s)
                        st.write("FAQ:")
                        for q in outline["faq"]:
                            st.write("- ", q)

        # Real-time trend section
        try:
            naver_only: List[str] = []
            google_only: List[str] = []
            if "naver" in providers_use:
                nav = NaverSuggestProvider()
                naver_only = nav.bulk_suggest(seeds)
            if "google" in providers_use:
                ggl = GoogleSuggestProvider()
                google_only = ggl.bulk_suggest(seeds, hl="ko")

            if "prev_naver" not in st.session_state:
                st.session_state["prev_naver"] = []
            if "prev_google" not in st.session_state:
                st.session_state["prev_google"] = []

            nav_delta = compute_trends(st.session_state["prev_naver"], naver_only, default_hot_terms())
            ggl_delta = compute_trends(st.session_state["prev_google"], google_only, default_hot_terms())

            cols = st.columns(2)
            with cols[0]:
                st.markdown("### Naver rising")
                st.write("Newly appeared:")
                st.write(nav_delta.new_suggestions[:20] or "(none)")
                st.write("Hot terms:")
                st.write([f"{k}×{v}" for k, v in nav_delta.hot_terms[:10]] or "(none)")
            with cols[1]:
                st.markdown("### Tistory(Google) rising")
                st.write("Newly appeared:")
                st.write(ggl_delta.new_suggestions[:20] or "(none)")
                st.write("Hot terms:")
                st.write([f"{k}×{v}" for k, v in ggl_delta.hot_terms[:10]] or "(none)")

            st.session_state["prev_naver"] = naver_only
            st.session_state["prev_google"] = google_only
        except Exception:
            pass


def main():  # override with extended UI
    load_env()
    st.set_page_config(page_title="Blog Keyword Analyzer", layout="wide")
    st.title("Blog Keyword Analyzer (Naver/Tistory)")

    with st.sidebar:
        st.header("설정")
        providers = st.multiselect("제공자", ["naver", "google"], default=["naver", "google"])
        depth = st.slider("확장 깊이", 1, 2, 2)
        profile = st.selectbox("프로필", ["", "travel", "food"], index=0)
        include_suffix = st.checkbox("롱테일 접미사 포함", value=False)
        limit = st.number_input("최대 후보 수", min_value=50, max_value=2000, value=400, step=50)
        top = st.number_input("미리보기 Top N", min_value=10, max_value=300, value=80, step=10)
        enrich_limit = st.number_input("API Enrich 제한", min_value=50, max_value=1000, value=200, step=50)
        platforms = st.multiselect("플랫폼", ["naver", "tistory"], default=["naver", "tistory"])
        st.divider()
        st.caption("실시간 트렌드 (자동완성 변화)")
        refresh = st.button("새로고침")

        st.markdown("---")
        st.subheader("추가 옵션")
        months = st.slider("기간(개월, UI용)", 3, 24, 12)
        min_volume = st.number_input("최소 검색량(PC+MO)", 0, 1_000_000, 50, 10)
        max_len = st.number_input("키워드 최대 글자수", 5, 40, 25, 1)
        ban_tokens = st.text_input("금지어(쉼표)", value="무료,다운로드,쿠폰,불법")
        beginner_mode = st.checkbox("초보자 가이드 보기", value=True)
        st.markdown("---")
        st.subheader("내 블로그 URL")
        nb_url = st.text_input("네이버 블로그 URL(선택)", value="")
        ts_url = st.text_input("티스토리 URL(선택)", value="")

    keys = _get_env_keys()

    seeds_text = st.text_area("시드 키워드(한 줄에 하나)", "봄 나들이\n봄 여행")
    run = st.button("실행")

    if not run:
        st.info("시드를 입력하고 [실행]을 눌러주세요.")
        return

    seeds = [normalize_query(s) for s in seeds_text.splitlines() if normalize_query(s)]
    if not seeds:
        st.warning("최소 1개의 시드 키워드를 입력하세요.")
        return
    providers_use = providers or ["google"]

    if "nonce" not in st.session_state:
        st.session_state["nonce"] = 0
    if refresh:
        st.session_state["nonce"] += 1

    with st.spinner("자동완성 수집 중..."):
        try:
            candidates, hit_counts = collect_suggestions_cached(
                seeds, providers_use, depth=depth, hl="ko", nonce=st.session_state["nonce"]
            )
        except Exception as e:  # noqa: BLE001
            st.error(f"자동완성 수집 오류: {e}")
            return

    if profile:
        candidates = unique_ordered(candidates + expand_with_profile(seeds, profile))
    elif include_suffix:
        candidates = unique_ordered(candidates + expand_with_suffixes(seeds))

    banned = {t.strip() for t in ban_tokens.split(",") if t.strip()}
    candidates = [c for c in candidates if len(c) <= int(max_len) and not any(b in c for b in banned)]
    if limit:
        candidates = candidates[: int(limit)]

    st.info(f"스코어링 및 API 메트릭 조회 중... (총 {len(candidates)}개)")
    enrichers = build_enrichers_from_env()
    if not enrichers:
        st.error("API 키를 찾을 수 없습니다. .env 또는 Streamlit Secrets에 NAVER_*/GOOGLE_* 값을 설정하세요.")
        st.stop()
    active = ", ".join(sorted(enrichers.keys())) or "(none)"
    st.success(f"활성화된 API: {active}")
    metrics_map: Dict[str, EnrichedMetrics] = enrich_keywords(candidates, enrichers, limit=int(enrich_limit))

    if not platforms:
        platforms = ["naver", "tistory"]
    scored: Dict[str, List[KeywordScore]] = {}
    for pf in platforms:
        scored[pf] = score_keywords_by_platform(
            candidates, hit_counts=hit_counts, metrics=metrics_map, platform=pf
        )

    def _est_clicks(pc: Optional[int], mo: Optional[int]) -> tuple[int, int]:
        pc_i = _to_int(pc)
        mo_i = _to_int(mo)
        return int(pc_i * 0.06), int(mo_i * 0.06)

    money_rows: List[Dict[str, Any]] = []
    for kw in candidates:
        m = metrics_map.get(kw)
        pc = getattr(m, "naver_monthly_pc", 0) if m else 0
        mo = getattr(m, "naver_monthly_mobile", 0) if m else 0
        cpc = getattr(m, "naver_cpc", 0.0) if m else 0.0
        pc_clk, mo_clk = _est_clicks(pc, mo)
        money_rows.append(
            {
                "relKeyword": kw,
                "intent": _simple_intent(kw),
                "monthlyPcQcCnt": pc,
                "monthlyMobileQcCnt": mo,
                "monthlyAvePcClkCnt": pc_clk,
                "monthlyAveMobileClkCnt": mo_clk,
                "plAvgCpc": cpc,
            }
        )

    tabs = st.tabs([
        "개요",
        "플랫폼 랭크",
        "타이틀/메타",
        "SERP 개요",
        "시즈널리티",
        "롱테일",
        "Google CSE",
        "트렌드",
    ])

    # A) 개요
    with tabs[0]:
        st.subheader("수익(가정)")
        colA, colB = st.columns(2)
        cpc_assume = colA.number_input("추정 CPC(원)", min_value=0, max_value=100000, value=70, step=10)
        rpm_bonus = colB.number_input("Ad/RPM 보정(원)", min_value=0, max_value=1_000_000, value=0, step=100)

        rows_out: List[Dict[str, Any]] = []
        total_expected = 0
        for r in money_rows:
            pc_clk = _to_int(r.get("monthlyAvePcClkCnt"))
            mo_clk = _to_int(r.get("monthlyAveMobileClkCnt"))
            rev = pc_clk + mo_clk
            rev = int(rev * int(cpc_assume) + int(rpm_bonus))
            total_expected += rev
            rows_out.append(
                {
                    "relKeyword": r["relKeyword"],
                    "intent": r["intent"],
                    "monthlyPcQcCnt": r["monthlyPcQcCnt"],
                    "monthlyMobileQcCnt": r["monthlyMobileQcCnt"],
                    "monthlyAvePcClkCnt": pc_clk,
                    "monthlyAveMobileClkCnt": mo_clk,
                    "예상_수익(원)": rev,
                }
            )

        st.metric("표시 키워드 수", len(rows_out))
        st.metric("예상 수익 합(표시 기준)", f"{total_expected:,}원")
        st.dataframe(sorted(rows_out, key=lambda x: x["예상_수익(원)"], reverse=True)[:30], use_container_width=True)

        st.markdown("### Outline 미리보기")
        if rows_out:
            sel_kw = rows_out[0]["relKeyword"]
            with st.expander(f"아웃라인: {sel_kw}"):
                outline = build_outline(sel_kw)
                st.write("Title:", outline["title"][0])
                st.write("Sections:")
                for s in outline["sections"]:
                    st.write("- ", s)
                st.write("FAQ:")
                for q in outline["faq"]:
                    st.write("- ", q)

        csv_bytes = to_csv_bytes(rows_out)
        st.download_button("CSV 다운로드(개요)", data=csv_bytes, file_name="overview.csv", mime="text/csv")

    # B) 플랫폼 랭크(간이)
    with tabs[1]:
        st.subheader("플랫폼 랭크(간이)")
        kw_rank = st.text_input("분석 키워드", value=seeds[0], key="rank_kw")
        g_api, g_cx = keys.get("GOOGLE_API_KEY", ""), keys.get("GOOGLE_CSE_CX", "")
        if st.button("랭크 조회"):
            if not g_api or not g_cx:
                st.warning("GOOGLE_API_KEY / GOOGLE_CSE_CX 설정 필요")
            else:
                items = google_cse_search(g_api, g_cx, kw_rank, num=10)
                st.dataframe(items or [], use_container_width=True)

                def find_rank(url_substr: str) -> Optional[int]:
                    if not url_substr:
                        return None
                    for i, it in enumerate(items, start=1):
                        if url_substr.lower() in str(it.get("link", "")).lower():
                            return i
                    return None

                nb_rank = find_rank(nb_url)
                ts_rank = find_rank(ts_url)
                col1, col2 = st.columns(2)
                col1.metric("네이버 블로그 순위", nb_rank if nb_rank else "없음")
                col2.metric("티스토리 순위", ts_rank if ts_rank else "없음")

    # C) 타이틀/메타 추천
    with tabs[2]:
        st.subheader("키워드별 타이틀/메타 제안")
        top_rows = money_rows[:50]
        recs: List[Dict[str, Any]] = []
        for i, r in enumerate(top_rows):
            kw = r["relKeyword"]
            it = r.get("intent", "일반")
            pc = _to_int(r.get("monthlyPcQcCnt", 0))
            mo = _to_int(r.get("monthlyMobileQcCnt", 0))
            pair = unique_title_meta_for_row(kw, it, pc, mo, rank=i)
            recs.append({"keyword": kw, "intent": it, "title": pair["title"], "meta": pair["meta"]})
        st.dataframe(recs, use_container_width=True)

    # D) SERP 개요(설명)
    with tabs[3]:
        st.subheader("작성 체크리스트(플랫폼)")
        st.write("- 서론: 문제/기대/대상 독자")
        st.write("- 본문(H2/H3): 체크리스트·비교·대안·FAQ")
        st.write("- 마무리: CTA(상/중/하), 내부/외부 링크")
        if beginner_mode:
            st.markdown("---")
            st.markdown("#### 초보자 체크리스트")
            c1, c2 = st.columns(2)
            with c1:
                st.checkbox("키워드 1개 = 글 1개", True, key="ck_kw1")
                st.checkbox("서론 3~5문장, 키워드 포함", True, key="ck_intro")
                st.checkbox("이미지 3~5개(ALT 필수)", False, key="ck_img")
            with c2:
                st.checkbox("H2/H3에 키워드/동의어", True, key="ck_h2")
                st.checkbox("상/중/하 CTA 배치", True, key="ck_cta")
                st.checkbox("내부링크 2~3개", False, key="ck_internal")

    # E) 시즈널리티(간이)
    with tabs[4]:
        st.subheader("시즈널리티(간이)")
        st.info("Naver DataLab 연동 전입니다. 추후 확장 가능합니다.")

    # F) 롱테일 제안
    with tabs[5]:
        st.subheader("롱테일 키워드 제안")
        default_mods = "추천, 후기, 비교, 방법, 팁, 체크리스트, 가성비, 예약, 메뉴, 가격, 후기, 브런치, 카페"
        mods_text = st.text_input("수정어(쉼표)", value=default_mods)
        min_hits = st.slider("최소 히트 수(중복 출현)", 1, 5, 1, 1)
        max_items = st.slider("최대 제안 수", 10, 500, 200, 10)

        def multi_prompt_autocomplete(seed_kw: str, modifiers: List[str], limit: int = 200) -> List[Dict[str, Any]]:
            prompts = [seed_kw]
            for m in modifiers:
                m = m.strip()
                if m:
                    prompts.append(f"{seed_kw} {m}")
            hits: Dict[str, int] = {}
            sample_prompt: Dict[str, str] = {}
            for p in prompts:
                sugs = fetch_naver_suggestions_raw(p, "100") + fetch_naver_suggestions_raw(p, "111")
                for s in sugs:
                    hits[s] = hits.get(s, 0) + 1
                    sample_prompt.setdefault(s, p)
            rows = [
                {"keyword": k, "hits": v, "prompt": sample_prompt.get(k, ""), "source": "naver_autocomplete"}
                for k, v in hits.items()
            ]
            rows.sort(key=lambda x: (x["hits"], x["keyword"]), reverse=True)
            return rows[:limit]

        if st.button("자동완성 기반 제안 수집"):
            modifiers = [t.strip() for t in mods_text.split(",") if t.strip()]
            ac_rows = multi_prompt_autocomplete(seeds[0], modifiers, limit=max_items)
            if not ac_rows:
                st.info("자동완성 결과가 없습니다. 수정어를 바꾸어 보세요.")
            else:
                ac_keywords = [r["keyword"] for r in ac_rows]
                ac_metrics = enrich_keywords(ac_keywords, enrichers, limit=len(ac_keywords))
                for r in ac_rows:
                    m = ac_metrics.get(r["keyword"]) if isinstance(ac_metrics, dict) else None
                    r["monthlyPcQcCnt"] = getattr(m, "naver_monthly_pc", 0) if m else 0
                    r["monthlyMobileQcCnt"] = getattr(m, "naver_monthly_mobile", 0) if m else 0
                st.dataframe([r for r in ac_rows if r["hits"] >= min_hits], use_container_width=True)

    # G) Google CSE
    with tabs[6]:
        st.subheader("Google CSE 결과 보기")
        kw_g = st.text_input("검색 키워드", value=seeds[0], key="g_kw")
        num_g = st.slider("검색 결과 수", 1, 10, 10, 1)
        g_api = keys.get("GOOGLE_API_KEY", "")
        g_cx = keys.get("GOOGLE_CSE_CX", "")
        if not g_api or not g_cx:
            st.info("GOOGLE_API_KEY / GOOGLE_CSE_CX가 필요합니다.")
        else:
            if st.button("CSE 조회"):
                items = google_cse_search(g_api, g_cx, kw_g, num=num_g)
                st.dataframe(items or [], use_container_width=True)

    # H) 트렌드(자동완성 변화)
    with tabs[7]:
        st.subheader("트렌드: 자동완성 변화")
        try:
            naver_only: List[str] = []
            google_only: List[str] = []
            if "naver" in providers_use:
                nav = NaverSuggestProvider()
                naver_only = nav.bulk_suggest(seeds)
            if "google" in providers_use:
                ggl = GoogleSuggestProvider()
                google_only = ggl.bulk_suggest(seeds, hl="ko")

            if "prev_naver" not in st.session_state:
                st.session_state["prev_naver"] = []
            if "prev_google" not in st.session_state:
                st.session_state["prev_google"] = []

            nav_delta = compute_trends(st.session_state["prev_naver"], naver_only, default_hot_terms())
            ggl_delta = compute_trends(st.session_state["prev_google"], google_only, default_hot_terms())

            cols = st.columns(2)
            with cols[0]:
                st.markdown("### Naver 상승")
                st.write("새로 등장:")
                st.write(nav_delta.new_suggestions[:20] or "(없음)")
                st.write("핫 토픽:")
                st.write([f"{k}:{v}" for k, v in nav_delta.hot_terms[:10]] or "(없음)")
            with cols[1]:
                st.markdown("### Tistory(Google) 상승")
                st.write("새로 등장:")
                st.write(ggl_delta.new_suggestions[:20] or "(없음)")
                st.write("핫 토픽:")
                st.write([f"{k}:{v}" for k, v in ggl_delta.hot_terms[:10]] or "(없음)")

            st.session_state["prev_naver"] = naver_only
            st.session_state["prev_google"] = google_only
        except Exception:
            pass

if __name__ == "__main__":  # pragma: no cover
    main()

