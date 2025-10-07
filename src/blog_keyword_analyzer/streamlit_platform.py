from __future__ import annotations

import csv
import io
import os
import sys
from typing import Dict, List, Tuple

import streamlit as st

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
from blog_keyword_analyzer.text_utils import normalize_query, unique_ordered
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


if __name__ == "__main__":  # pragma: no cover
    main()

