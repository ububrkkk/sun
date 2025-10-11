from __future__ import annotations

import csv
import io
import os
from typing import Dict, List, Tuple

import streamlit as st

from .env import load_env
from .expansion import expand_with_profile, expand_with_suffixes
from .providers import GoogleSuggestProvider, NaverSuggestProvider
from .text_utils import normalize_query, unique_ordered
from .enrichers import build_enrichers_from_env, enrich_keywords, EnrichedMetrics
from .monetization import MonetizationParams, monetize_keywords


@st.cache_data(show_spinner=False, ttl=30)
def collect_suggestions(
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


def to_csv_bytes(rows: List[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


def main() -> None:
    load_env()
    st.set_page_config(page_title="Keyword Monetization Studio", layout="wide")
    st.title("Keyword Monetization Studio")

    with st.sidebar:
        st.header("Collect")
        providers = st.multiselect("Providers", ["naver", "google"], default=["naver", "google"])
        depth = st.slider("Expansion depth", 1, 2, 2)
        profile = st.selectbox("Profile", ["", "travel", "food"], index=0)
        include_suffix = st.checkbox("Include generic suffixes", value=False)
        limit = st.number_input("Max suggestions", min_value=50, max_value=2000, value=400, step=50)
        enrich_limit = st.number_input("API enrich limit", min_value=50, max_value=1000, value=300, step=50)
        st.divider()
        st.header("Monetization")
        capture = st.slider("Traffic capture %", 1, 50, 15, 1)
        pv_per_visit = st.number_input("Pageviews/visitor", min_value=1.0, max_value=5.0, value=1.3, step=0.1)
        ecpm = st.number_input("Display eCPM (KRW)", min_value=0, max_value=100_000, value=2500, step=100)
        aff_cvr = st.number_input("Affiliate CVR %", min_value=0.0, max_value=30.0, value=1.5, step=0.1)
        aff_comm = st.number_input("Affiliate commission (KRW)", min_value=0, max_value=1_000_000, value=1500, step=100)
        min_monthly = st.number_input("Min monthly search", min_value=0, max_value=1_000_000, value=50, step=10)
        exclude = st.text_input("Exclude tokens (comma)", value="무료,쿠폰")
        top_n = st.number_input("Show Top N", min_value=10, max_value=500, value=100, step=10)

    seeds_text = st.text_area("Seed keywords (one per line)", "서울 맛집\n부산 카페")
    run = st.button("Run")
    if not run:
        st.info("Enter seeds then click Run.")
        return

    seeds = [normalize_query(s) for s in seeds_text.splitlines() if normalize_query(s)]
    if not seeds:
        st.warning("Please enter at least one seed.")
        return

    providers_use = providers or ["google"]

    if "nonce" not in st.session_state:
        st.session_state["nonce"] = 0
    st.session_state["nonce"] += 1

    with st.spinner("Collecting suggestions..."):
        cands, _ = collect_suggestions(seeds, providers_use, depth=depth, hl="ko", nonce=st.session_state["nonce"])  # noqa: E501

    if profile:
        cands = unique_ordered(cands + expand_with_profile(seeds, profile))
    elif include_suffix:
        cands = unique_ordered(cands + expand_with_suffixes(seeds))
    if limit:
        cands = cands[: int(limit)]

    st.info(f"Enriching with APIs... ({len(cands)} keywords)")
    enrichers = build_enrichers_from_env()
    if not enrichers:
        st.error("API keys required. Set NAVER_AD_*/GOOGLE_* in .env or Secrets.")
        st.stop()
    active = ", ".join(sorted(enrichers.keys())) or "(none)"
    st.success(f"Active APIs: {active}")
    metrics_map: Dict[str, EnrichedMetrics] = enrich_keywords(cands, enrichers, limit=int(enrich_limit))
    # hint if monthly metrics missing
    has_monthly = any(((getattr(m, "naver_monthly_pc", 0) or 0) + (getattr(m, "naver_monthly_mobile", 0) or 0)) > 0 for m in metrics_map.values())
    if not has_monthly:
        st.info("No NAVER monthly metrics available. Set NAVER_AD_* keys for better estimates.")

    params = MonetizationParams(
        capture_pct=float(capture),
        pv_per_visit=float(pv_per_visit),
        ecpm=float(ecpm),
        aff_cvr_pct=float(aff_cvr),
        aff_commission=float(aff_comm),
    )
    exclude_tokens = [t.strip() for t in (exclude or "").split(",") if t.strip()]
    rows = monetize_keywords(cands, metrics_map, params=params, min_monthly=int(min_monthly), exclude_tokens=exclude_tokens)
    total = sum(int(r.get("est_total_rev", 0)) for r in rows)

    st.metric("Keywords modeled", len(rows))
    st.metric("Total estimated revenue (KRW)", f"{total:,}")
    st.dataframe(rows[: int(top_n)], use_container_width=True)
    st.download_button("Download CSV", data=to_csv_bytes(rows), file_name="monetization.csv", mime="text/csv")

    st.caption("Assumptions are sliders above. Use NAVER CPC as a commercial intent signal.")
