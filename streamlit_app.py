# -*- coding: utf-8 -*-
import os
import time
import datetime as dt
from typing import Dict, Any, List, Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# 내부 모듈 (실데이터만 사용)
from clients.searchad import NaverSearchAdClient
from clients.datalab import NaverDataLabClient

from core.recommend import apply_filters, annotate_intent
from core.scoring import rank_keywords
from core.trends import rising_from_datalab
from core.seasonality import seasonality_table, seasonal_index
from core.export_excel import export_keyword_report
from core.longtail import suggest_longtails
from core.rank_split import naver_platform_ranks  # 네이버/티스토리 순위 분리

# 페이지 설정
st.set_page_config(page_title="Naver Keyword Analyzer - 블로그 실전", layout="wide")
st.title("🚀 Naver Keyword Analyzer - 블로그 실전 (실시간/실데이터)")

# 외부 API 헬퍼
import requests
from bs4 import BeautifulSoup

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

def fetch_naver_suggestions(query: str, st_code: str = "100") -> list:
    """네이버 자동완성(실시간) 제안어."""
    try:
        url = "https://ac.search.naver.com/nx/ac"
        params = {
            "q": query,
            "st": st_code,  # 100/111 등
            "r_format": "json",
            "r_enc": "utf-8",
            "frm": "nv",
            "ans": "2",
            "r_lt": "1",
        }
        r = requests.get(url, params=params, timeout=6, headers={"User-Agent": _UA})
        r.raise_for_status()
        j = r.json()
        out = []
        items = (j.get("items") or [])
        if items:
            for entry in items[0]:
                if isinstance(entry, list) and entry:
                    s = str(entry[0]).strip()
                    if s:
                        out.append(s)
        return out
    except Exception:
        return []

def google_cse_search(api_key: str, cx: str, q: str, num: int = 10) -> list:
    """Google CSE 결과 (Top N)."""
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "cx": cx, "q": q, "num": min(max(num, 1), 10)}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": _UA})
        r.raise_for_status()
        j = r.json()
        items = j.get("items") or []
        return [{"title": it.get("title"), "link": it.get("link"), "snippet": it.get("snippet")} for it in items]
    except Exception:
        return []

def kakao_blog_search(api_key: str, query: str, size: int = 10, page: int = 1, sort: str = "recency") -> list:
    """Kakao 블로그 검색 (티스토리 포함)."""
    try:
        url = "https://dapi.kakao.com/v2/search/blog"
        params = {"query": query, "size": min(max(size, 1), 50), "page": max(page, 1), "sort": sort}
        headers = {"Authorization": f"KakaoAK {api_key}", "User-Agent": _UA}
        r = requests.get(url, params=params, headers=headers, timeout=8)
        r.raise_for_status()
        j = r.json()
        return j.get("documents") or []
    except Exception:
        return []

def fetch_url_html(url: str, timeout: int = 10) -> str:
    """임의 URL HTML."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
        r.raise_for_status()
        return r.text
    except Exception:
        return ""

def analyze_html_structure(html: str) -> Dict[str, int]:
    """간단 HTML 구조 메트릭(단어수/H2/H3/IMG/TABLE)."""
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

# 환경/유틸
def ensure_env() -> Dict[str, str]:
    """secrets → .env → os.environ 순서로 읽기."""
    load_dotenv()

    def getv(name: str) -> str:
        try:
            return st.secrets.get(name, "") or os.getenv(name, "")
        except Exception:
            return os.getenv(name, "")

    keys = {
        "NAVER_AD_API_KEY": getv("NAVER_AD_API_KEY"),
        "NAVER_AD_SECRET_KEY": getv("NAVER_AD_SECRET_KEY"),
        "NAVER_AD_CUSTOMER_ID": getv("NAVER_AD_CUSTOMER_ID"),
        "NAVER_OPENAPI_CLIENT_ID": getv("NAVER_OPENAPI_CLIENT_ID"),
        "NAVER_OPENAPI_CLIENT_SECRET": getv("NAVER_OPENAPI_CLIENT_SECRET"),
        "GOOGLE_API_KEY": getv("GOOGLE_API_KEY"),
        "GOOGLE_CSE_CX": getv("GOOGLE_CSE_CX"),
        "KAKAO_REST_API_KEY": getv("KAKAO_REST_API_KEY"),
    }
    # 필수: 네이버 5개만
    required = [
        "NAVER_AD_API_KEY",
        "NAVER_AD_SECRET_KEY",
        "NAVER_AD_CUSTOMER_ID",
        "NAVER_OPENAPI_CLIENT_ID",
        "NAVER_OPENAPI_CLIENT_SECRET",
    ]
    missing = [k for k in required if not keys.get(k)]
    if missing:
        st.error("API 키가 없습니다: " + ", ".join(missing) + " — Secrets 또는 .env에 추가하세요.")
        return {}
    return keys

def _to_int(x):
    try:
        s = str(x)
        if "<" in s:
            return 0
        return int(float(s.replace(",", "")))
    except Exception:
        return 0

def fmt_won(x: int) -> str:
    return f"{int(x):,}원"

def fmt_int(x: int) -> str:
    return f"{int(x):,}"

def call_with_backoff(fn, *args, tries=3, base_sleep=1.0, **kwargs):
    """간단 재시도 백오프."""
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(base_sleep * (2 ** i))

# 초보자 가이드 보조
def beginner_article_type(intent: str) -> str:
    if intent in ("거래", "상업"):
        return "리뷰/비교/구매가이드"
    if intent == "내비게이션":
        return "동선/코스/주차 안내"
    return "종합 가이드/체크리스트"

def basic_ad_positions(platform: str) -> str:
    if platform.lower().startswith("tistory"):
        return "상단 인피드 1 · 중단 인피드 1 · 하단 배너 1"
    return "상단 이미지 하단 1 · 중간 문단 사이 1 · 결론 직전 1"

def make_sponsor_pitch(brand: str, keyword: str, platform: str) -> str:
    brand = (brand or "파트너사").strip()
    platform = (platform or "블로그").strip()
    return (
        f"안녕하세요, {brand} 담당자님.\n\n"
        f"'{keyword}' 주제로 {platform}를 운영하며 독자에게 실전 정보를 제공합니다.\n"
        f"이번 주제와 {brand}의 제품/서비스가 잘 맞아 체험 리뷰/가이드 협업을 제안드립니다.\n\n"
        "제공 가능: 촬영 이미지·체크리스트 PDF·가격/옵션 비교표·CTA 배치(상/중/하)\n"
        "노출: 상단 요약·중단 비교표·결론 CTA, 내부/외부 링크 연계\n"
        "일정: 초안 3일, 피드백 반영 포함 7일 내 게시\n\n"
        "검토 부탁드립니다. 감사합니다."
    )

def unique_title_meta_for_row(kw: str, intent: str, vol_pc: int, vol_mo: int, rank: int) -> Dict[str, str]:
    """키워드별 중복 없는 제목/메타 초안."""
    base = kw.strip()
    total = (vol_pc or 0) + (vol_mo or 0)
    year = dt.date.today().year
    if intent in ("거래", "상업"):
        patterns = [
            f"{base} {year} 최저가 가이드 | 가격·옵션 비교표",
            f"{base} 실패 없는 구매 체크리스트 12가지",
            f"{base} TOP7 모델 비교 | 예산·용도별 추천",
            f"{base} 실사용 후기 핵심만 | 장단점 요약",
            f"{base} 신상 vs 가성비 | 누구에게 무엇이 좋나",
        ]
        metas = [
            f"{base} 사기 전 꼭 확인할 체크리스트와 가격/옵션 비교표를 담았습니다. 쿠폰/환불 팁 포함.",
            f"{base} 구매 전 궁금한 것만 모아 간단히 정리했습니다. 사용 기준 장단점과 A/S 요령까지.",
        ]
    elif intent == "내비게이션":
        patterns = [
            f"{base} 가는 법·주차·동선 10분컷 | 처음 가는 사람용",
            f"{base} 당일 코스 추천 | 시간대별 동선표",
            f"{base} 교통/주차 현실정리 | 피크시간 회피 팁",
        ]
        metas = [
            f"{base} 처음 가도 헤매지 않게 동선/주차/소요시간을 한 번에 정리했습니다. 지도·비용·주의사항 포함.",
        ]
    else:
        patterns = [
            f"{base} 완벽 가이드 | 핵심만 빠르게 정리",
            f"{base} 입문서 | 꼭 알아야 할 개념·실수·꿀팁",
            f"{base} 전문가가 먼저 보는 체크포인트 15가지",
            f"{base} Q&A 20문20답 | 모르면 손해보는 포인트",
        ]
        metas = [
            f"{base}를 처음부터 끝까지 한 번에. 핵심 개념, 케이스, 자주 하는 실수를 간단히 정리했습니다.",
        ]
    if total >= 20000:
        patterns = [p.replace("가이드", "초격차 가이드").replace("입문서", "실전 입문서") for p in patterns]
    title = patterns[rank % len(patterns)]
    meta = metas[rank % len(metas)]
    if len(title) > 38:
        title = title[:36] + "…"
    if len(meta) > 110:
        meta = meta[:108] + "…"
    return {"title": title, "meta": meta}

# 사이드바
with st.sidebar:
    st.header("설정")
    seed = st.text_input("씨앗 키워드", value=st.session_state.get("_seed", "마카오 여행"))
    months = st.slider("DataLab 기간(개월)", 3, 24, 12)
    min_volume = st.number_input("최소 검색량(PC+MO)", 0, 1_000_000, 50, 10)
    max_len = st.number_input("키워드 최대 글자수", 5, 40, 25, 1)
    ban_tokens = st.text_input("금칙어(쉼표)", value="무료,다운로드,토렌트,성인")
    device = st.selectbox("디바이스", ["", "pc", "mo"], index=0)
    topn = st.slider("표시 개수(Top N)", 10, 500, 150, 10)

    st.markdown("---")
    beginner_mode = st.checkbox("초보자 모드 (가이드 표시)", value=st.session_state.get("_beginner", True))

    st.markdown("---")
    cpc_assume = st.number_input("(선택) CPC 가정(원) — 표시용", 10, 5000, 80, 10)
    rpm_bonus = st.number_input("(선택) 보너스 수익(원/월) — 표시용", 0, 10_000_000, 0, 1000)

    st.session_state.update({
        "_seed": seed, "_months": months, "_min_volume": min_volume,
        "_max_len": max_len, "_ban_tokens": ban_tokens,
        "_device": device, "_topn": topn, "_cpc": cpc_assume,
        "_rpm": rpm_bonus,
        "_beginner": beginner_mode
    })
    run = st.button("분석 실행 / 갱신", key="btn_run")

# 실행
if run or st.session_state.get("_ran_once", False):
    st.session_state["_ran_once"] = True
    keys = st.session_state.get("_naver_keys") or ensure_env()
    if not keys:
        st.stop()
    st.session_state["_naver_keys"] = keys

    ad = NaverSearchAdClient(keys["NAVER_AD_API_KEY"], keys["NAVER_AD_SECRET_KEY"], keys["NAVER_AD_CUSTOMER_ID"])
    dl = NaverDataLabClient(keys["NAVER_OPENAPI_CLIENT_ID"], keys["NAVER_OPENAPI_CLIENT_SECRET"])

    # 1) SearchAd 연관키워드 → 필터/랭크/의도
    with st.spinner("네이버 검색광고: 연관 키워드 실시간 수집 중…"):
        rel = call_with_backoff(ad.related_keywords, seed, show_detail=True, max_rows=1000)
        filtered = apply_filters(rel, min_volume, max_len, [t.strip() for t in ban_tokens.split(",") if t.strip()])
        ranked = rank_keywords(filtered, mobile_weight=1.2)
        annotate_intent(ranked)
        if not ranked:
            st.warning("조건에 맞는 키워드가 없습니다. 필터를 완화해 보세요.")
            st.stop()
        df = pd.DataFrame(ranked[:topn])

    # 2) DataLab 월간 트렌드
    with st.spinner("네이버 데이터랩: 트렌드(월 단위) 조회 중…"):
        end = dt.date.today()
        start = end - dt.timedelta(days=30 * months)
        kw_list = df["relKeyword"].head(5).tolist() or [seed]
        try:
            res = call_with_backoff(
                dl.trend, kw_list, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                time_unit="month", device=device
            )
        except Exception as e:
            res = {}
            st.info(f"DataLab 호출 실패(계속 진행): {e}")

    rising = rising_from_datalab(res, topn=20) if res else pd.DataFrame()

    # 3) 수익(표시용) 계산
    money_df = df.copy()
    for c in ["monthlyAvePcClkCnt", "monthlyAveMobileClkCnt"]:
        if c not in money_df.columns:
            money_df[c] = 0
        money_df[c] = money_df[c].apply(_to_int)
    money_df["월간_클릭_합계"] = money_df["monthlyAvePcClkCnt"] + money_df["monthlyAveMobileClkCnt"]
    money_df["예상_매출(원)"] = money_df["월간_클릭_합계"] * int(cpc_assume) + int(rpm_bonus)
    total_expected = int(money_df.get("예상_매출(원)", pd.Series(dtype=int)).sum()) if not money_df.empty else 0

    # 탭 구성
    tabs = st.tabs([
        "시작하기",
        "플랫폼별 순위",
        "제목/메타 추천",
        "SERP 템플릿",
        "시즌성",
        "롱테일 추천",
        "구글 순위",
        "인기/인구통계",
    ])

    # A) 시작하기
    with tabs[0]:
        if st.session_state.get("_beginner"):
            st.markdown("### 👶 초보자 가이드")
            st.info("1) 씨앗 입력 → 2) [분석 실행 / 갱신] → 3) [롱테일 추천]/[제목/메타 추천] 참고 → 4) 발행 전 체크리스트 확인 → 5) [플랫폼별/구글 순위] 확인")
            with st.expander("발행 전 체크리스트", expanded=True):
                c1, c2 = st.columns(2)
                with c1:
                    st.checkbox("키워드 1개 = 글 1개", True, key="ck_kw1")
                    st.checkbox("서론 3~5문장, 핵심 요약 상단", True, key="ck_intro")
                    st.checkbox("이미지 3~5장(ALT 포함)", False, key="ck_img")
                with c2:
                    st.checkbox("중간 H2/H3에 키워드/변형", True, key="ck_h2")
                    st.checkbox("상/중/하 CTA 배치", True, key="ck_cta")
                    st.checkbox("내부링크 2~3개", False, key="ck_internal")
            st.markdown("---")

        st.subheader("📌 개요(표시용)")
        col1, col2 = st.columns(2)
        col1.metric("표시 키워드 수", fmt_int(len(money_df)))
        col2.metric("월 예상 매출 합계(표시용)", fmt_won(total_expected))

        show = money_df.sort_values("예상_매출(원)", ascending=False).head(30)[[
            "relKeyword", "intent", "monthlyPcQcCnt", "monthlyMobileQcCnt",
            "monthlyAvePcClkCnt", "monthlyAveMobileClkCnt", "예상_매출(원)"
        ]].rename(columns={
            "relKeyword": "키워드", "intent": "의도", "monthlyPcQcCnt": "PC검색량", "monthlyMobileQcCnt": "MO검색량",
            "monthlyAvePcClkCnt": "PC클릭", "monthlyAveMobileClkCnt": "MO클릭"
        })
        for c in ["PC검색량", "MO검색량", "PC클릭", "MO클릭"]:
            show[c] = show[c].apply(_to_int).apply(fmt_int)
        show["예상_매출(원)"] = show["예상_매출(원)"].apply(fmt_won)
        st.dataframe(show, use_container_width=True)

        st.markdown("#### ✍️ 실제 작성 가이드(상위 10)")
        plan_rows = []
        for _, r0 in money_df.sort_values("예상_매출(원)", ascending=False).head(10).iterrows():
            it = r0.get("intent", "정보")
            plan_rows.append({
                "키워드": r0["relKeyword"],
                "의도": it,
                "권장 글 유형": beginner_article_type(it),
                "티스토리 광고": basic_ad_positions("Tistory"),
                "네이버 광고": basic_ad_positions("Naver Blog"),
            })
        st.dataframe(pd.DataFrame(plan_rows), use_container_width=True)

        st.markdown("### 📈 DataLab 급상승(월)")
        if isinstance(rising, pd.DataFrame) and not rising.empty:
            st.dataframe(rising, use_container_width=True)
        else:
            st.info("표시할 급상승 결과가 없습니다.")

        st.markdown("---")
        if st.button("⬇️ 엑셀 내보내기(수익 포함)", key="btn_export_overview"):
            try:
                xlsx = export_keyword_report(seed, money_df.to_dict("records"), rising)
                st.success(f"저장 완료: {xlsx}")
            except Exception as e:
                st.error(f"엑셀 저장 실패: {e}")

        st.markdown("### 🤝 협찬 제안서 만들기")
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            brand = st.text_input("브랜드/업체명", value="예) 여행사 A")
        with c2:
            plat = st.selectbox("플랫폼", ["티스토리", "네이버 블로그"])
        with c3:
            pick_kw = st.selectbox("대상 키워드", options=money_df["relKeyword"].head(10).tolist() or [seed])
        pitch = make_sponsor_pitch(brand, pick_kw, plat)
        st.text_area("제안서 초안", value=pitch, height=180)
        st.download_button("⬇️ 텍스트 저장", data=pitch.encode("utf-8"), file_name="sponsor_pitch.txt", key="dl_pitch")

        st.markdown("### 🗓️ 콘텐츠 캘린더 (4주 제안)")
        weeks = [
            ("1주차", "체크리스트/준비물형 2개 + Q&A 1개"),
            ("2주차", "구매가이드 1개 + 리뷰 1개 + 비교 1개"),
            ("3주차", "대안/비교 2개 + 노하우 1개"),
            ("4주차", "케이스 스터디 2개 + 요약 허브 1개"),
        ]
        st.write("\n".join(f"- **{w}**: {plan}" for w, plan in weeks))

    # B) 플랫폼별 순위 (네이버 SERP)
    with tabs[1]:
        st.subheader("🔎 플랫폼별 순위 (네이버 SERP)")
        kw_rank = st.text_input("분석 키워드", value=seed, key="rank_kw")
        if st.button("순위 조회", key="rank_check_main"):
            try:
                r = naver_platform_ranks(
                    kw_rank,
                    keys.get("NAVER_OPENAPI_CLIENT_ID", ""),
                    keys.get("NAVER_OPENAPI_CLIENT_SECRET", ""),
                    display=50
                )
                colA, colB = st.columns(2)
                nav_r = r["ranks"]["naver"]; tis_r = r["ranks"]["tistory"]
                colA.metric("네이버 블로그 최초 노출", f"{nav_r}위" if nav_r else "미노출")
                colB.metric("티스토리 최초 노출", f"{tis_r}위" if tis_r else "미노출")

                st.markdown("**네이버 블로그 상위 URL**")
                if r["naver_top"]:
                    st.write("\n".join(f"{x['rank']}위 · {x['url']}" for x in r["naver_top"]))
                else:
                    st.write("결과 없음")

                st.markdown("---")
                st.markdown("**티스토리 상위 URL**")
                if r["tistory_top"]:
                    st.write("\n".join(f"{x['rank']}위 · {x['url']}" for x in r["tistory_top"]))
                else:
                    st.write("결과 없음")
            except Exception as e:
                st.error(f"순위 조회 실패: {e}")

        st.markdown("---")
        st.subheader("🟠 티스토리/블로그 검색 (Kakao Search)")
        kakao_key = st.text_input("Kakao REST API Key", value=keys.get("KAKAO_REST_API_KEY", ""), type="password")
        kw_kakao = st.text_input("검색 키워드(카카오)", value=kw_rank)
        size_kakao = st.slider("가져올 개수", 1, 50, 10, 1)
        if st.button("카카오 블로그 검색", key="btn_kakao_search"):
            if not kakao_key:
                st.warning("Kakao REST API Key가 필요합니다.")
            else:
                docs = kakao_blog_search(kakao_key, kw_kakao, size=size_kakao, page=1, sort="recency")
                if not docs:
                    st.info("검색 결과가 없습니다.")
                else:
                    only_tistory = st.checkbox("티스토리만 보기", value=True)
                    rows = []
                    for d in docs:
                        url = d.get("url") or d.get("blogurl")
                        if only_tistory and (not url or "tistory.com" not in url):
                            continue
                        rows.append({
                            "title": d.get("title"),
                            "url": url,
                            "blogname": d.get("blogname"),
                            "datetime": d.get("datetime"),
                            "contents": d.get("contents"),
                        })
                    if rows:
                        st.dataframe(pd.DataFrame(rows), use_container_width=True)
                    else:
                        st.info("티스토리 결과가 없습니다. 전체 보기로 확인해보세요.")

        st.markdown("#### 🧩 상위 결과 구조 분석(간단)")
        analyze_count = st.slider("분석할 상위 결과 수", 1, 20, 5, 1)
        if st.button("상위 결과 구조 분석", key="btn_kakao_analyze"):
            if not kakao_key:
                st.warning("Kakao REST API Key가 필요합니다.")
            else:
                docs = kakao_blog_search(kakao_key, kw_kakao, size=analyze_count, page=1, sort="accuracy")
                if not docs:
                    st.info("분석할 결과가 없습니다.")
                else:
                    met_rows = []
                    for d in docs:
                        url = d.get("url") or d.get("blogurl")
                        html = fetch_url_html(url)
                        m = analyze_html_structure(html)
                        met_rows.append({
                            "title": d.get("title"),
                            "url": url,
                            "words": m["words"],
                            "h2": m["h2"],
                            "h3": m["h3"],
                            "img": m["img"],
                            "table": m["table"],
                        })
                    dfm = pd.DataFrame(met_rows)
                    st.dataframe(dfm, use_container_width=True)
                    if not dfm.empty:
                        st.markdown("**평균값(대략)**")
                        avg = dfm[["words", "h2", "h3", "img", "table"]].mean(numeric_only=True).round(1)
                        st.write(avg.to_frame().T)
                    ideas = [f"- {d.get('title')}: {d.get('contents')[:80]}…" for d in docs]
                    st.markdown("**빠른 아이디어**")
                    st.write("\n".join(ideas))

    # C) 제목/메타 추천
    with tabs[2]:
        st.subheader("🧲 키워드별 고유 제목/메타")
        rows = []
        for i, r in money_df.head(50).reset_index(drop=True).iterrows():
            kw = r["relKeyword"]; it = r.get("intent", "정보")
            pc = _to_int(r.get("monthlyPcQcCnt", 0)); mo = _to_int(r.get("monthlyMobileQcCnt", 0))
            pair = unique_title_meta_for_row(kw, it, pc, mo, rank=i)
            rows.append({"키워드": kw, "의도": it, "제목": pair["title"], "메타디스크립션": pair["meta"]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # D) SERP 템플릿 (선택)
    with tabs[3]:
        st.subheader("🧠 상위 구조 초안 (플랫폼별)")
        st.caption("간단한 구조 초안/아이디어만 제공합니다.")
        st.write("- 서론: 요약(핵심/목차/주의사항)")
        st.write("- 본문(H2/H3): 비교표·체크리스트·케이스·FAQ")
        st.write("- 결론: CTA(상/중/하), 내부/외부 링크")

    # E) 시즌성
    with tabs[4]:
        st.subheader("📅 시즌성/지수")
        try:
            sea_df = seasonality_table(res) if res else pd.DataFrame()
            idx = seasonal_index(sea_df) if not sea_df.empty else pd.DataFrame()
            if not idx.empty:
                view = idx.copy()
                for c in view.columns:
                    view[c] = view[c].apply(lambda x: f"{float(x)*100:.0f}%" if pd.notnull(x) else "-")
                st.markdown("**월별 상대 지수(1.0 = 보통)**")
                st.dataframe(view, use_container_width=True)
            else:
                st.info("표시할 시즌성 데이터가 없습니다.")
        except Exception as e:
            st.warning(f"시즌성 계산 오류: {e}")

    # F) 롱테일 추천
    with tabs[5]:
        st.subheader("🌱 롱테일 키워드 추천")
        method = st.radio("방식", ["SearchAd API 기반", "자동완성 멀티-프롬프트(실시간)"], horizontal=True)

        def _export_df(df_out: pd.DataFrame, prefix: str) -> None:
            try:
                fname = f"{prefix}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                df_out.to_excel(fname, index=False)
                st.success(f"저장 완료: {fname}")
            except Exception as e:
                st.error(f"저장 실패: {e}")

        if method == "SearchAd API 기반":
            min_clicks = st.number_input("최소 월클릭(필터)", 0, 100000, 20, 10)
            lt_df = suggest_longtails(df, min_clicks=min_clicks, max_items=300)
            if lt_df.empty:
                st.info("조건을 만족하는 롱테일이 없습니다. 필터를 낮춰보세요.")
            else:
                show_lt = lt_df.sort_values("월클릭", ascending=False).reset_index(drop=True)
                st.dataframe(show_lt, use_container_width=True)
                if st.button("⬇️ 롱테일 추천 엑셀 저장", key="btn_lt_export_api"):
                    _export_df(show_lt, "longtails_api")
        else:
            st.caption("• 여러 프롬프트로 네이버 자동완성을 조회해, 실제 제안만 모아 추천합니다 (더미 없음).")
            default_mods = "추천, 순위, 후기, 가격, 할인, 패키지, 일정, 코스, 가성비, 숙소, 맛집, 아이, 커플, 부모님, 1박2일, 2박3일, 당일, 9월, 10월, 주말"
            mods_text = st.text_input("프롬프트(쉼표)", value=default_mods, help="각 항목은 씨앗 키워드와 함께 자동완성 조회에 쓰입니다.")
            min_hits = st.slider("최소 히트 수(여러 프롬프트에 중복 등장)", 1, 5, 1, 1)
            max_items = st.slider("최대 추천 개수", 10, 500, 200, 10)

            def multi_prompt_autocomplete(seed_kw: str, modifiers: List[str], limit: int = 200) -> pd.DataFrame:
                prompts = [seed_kw]
                for m in modifiers:
                    m = m.strip()
                    if not m:
                        continue
                    prompts.append(f"{seed_kw} {m}")
                hits: Dict[str, int] = {}
                sample_prompt: Dict[str, str] = {}
                for p in prompts:
                    sugs = fetch_naver_suggestions(p, "100") + fetch_naver_suggestions(p, "111")
                    for s in sugs:
                        hits[s] = hits.get(s, 0) + 1
                        sample_prompt.setdefault(s, p)
                rows = [
                    {"keyword": k, "hits": v, "prompt": sample_prompt.get(k, ""), "source": "naver_autocomplete"}
                    for k, v in hits.items()
                ]
                out = pd.DataFrame(rows).sort_values(["hits", "keyword"], ascending=[False, True]).head(limit).reset_index(drop=True)
                return out

            if st.button("실시간 추천 가져오기", key="btn_mp_fetch"):
                modifiers = [t.strip() for t in mods_text.split(",") if t.strip()]
                ac_df = multi_prompt_autocomplete(seed, modifiers, limit=max_items)
                if ac_df.empty:
                    st.info("실시간 제안이 없습니다. 프롬프트를 바꾸거나 보조 씨앗을 사용해 보세요.")
                else:
                    vol_map: Dict[str, Dict[str, Any]] = {}
                    try:
                        rel2 = ad.related_keywords(seed, show_detail=True, max_rows=2000)
                        for r2 in rel2 or []:
                            k2 = str(r2.get("relKeyword", "")).strip()
                            if k2:
                                vol_map[k2] = r2
                    except Exception:
                        pass
                    ac_df["monthlyPcQcCnt"] = ac_df["keyword"].map(lambda k: _to_int((vol_map.get(k) or {}).get("monthlyPcQcCnt")))
                    ac_df["monthlyMobileQcCnt"] = ac_df["keyword"].map(lambda k: _to_int((vol_map.get(k) or {}).get("monthlyMobileQcCnt")))
                    ac_df["totalVolume"] = ac_df["monthlyPcQcCnt"] + ac_df["monthlyMobileQcCnt"]
                    show_ac = ac_df[ac_df["hits"] >= min_hits].sort_values(["hits", "totalVolume"], ascending=[False, False])
                    st.dataframe(show_ac, use_container_width=True)
                    if st.button("⬇️ 롱테일 추천 엑셀 저장", key="btn_lt_export_ac"):
                        _export_df(show_ac, "longtails_autocomplete")

    # G) 구글 순위 (CSE)
    with tabs[6]:
        st.subheader("🔎 구글 검색 순위 (CSE)")
        kw_g = st.text_input("구글 순위 키워드", value=seed, key="g_kw")
        num_g = st.slider("검사 개수(구글)", 1, 10, 10, 1)
        g_api = keys.get("GOOGLE_API_KEY", "")
        g_cx = keys.get("GOOGLE_CSE_CX", "")
        if not g_api or not g_cx:
            st.info("GOOGLE_API_KEY / GOOGLE_CSE_CX가 없으면 CSE를 사용할 수 없습니다.")
        else:
            if st.button("구글 순위 조회", key="btn_google_rank"):
                try:
                    items = google_cse_search(g_api, g_cx, kw_g, num=num_g)
                    if not items:
                        st.info("결과가 없습니다.")
                    else:
                        st.markdown("**Top 결과**")
                        st.dataframe(pd.DataFrame(items), use_container_width=True)

                        col1, col2 = st.columns(2)

                except Exception as e:
                    st.error(f"구글 순위 조회 실패: {e}")

    # H) 인기/인구통계 (실시간 제안 + DataLab)
    with tabs[7]:
        st.subheader("🔥 인기/인구통계 (실시간 + DataLab)")
        sug = list(dict.fromkeys(
            fetch_naver_suggestions(seed, "100") + fetch_naver_suggestions(seed, "111")
        ))[:10]
        if sug:
            st.markdown("**네이버 자동완성(실시간) 제안 Top 10**")
            st.write("\n".join(f"- {s}" for s in sug))
        else:
            st.info("실시간 제안이 없습니다. 보조 씨앗을 추가해 보세요.")

        try:
            end = dt.date.today()
            start = end - dt.timedelta(days=30)

            def _avg_ratio(gender: str) -> float:
                tr = dl.trend([seed], start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'), time_unit='date', gender=gender)
                rs = tr.get('results') or []
                if not rs or not rs[0].get('data'):
                    return 0.0
                vals = [x.get('ratio') or 0 for x in rs[0]['data']]
                return sum(vals)/len(vals) if vals else 0.0

            male = _avg_ratio('m')
            female = _avg_ratio('f')
            st.markdown("**성별 비중(상대값)**")
            st.bar_chart(pd.DataFrame({"ratio": [male, female]}, index=["남성", "여성"]))

            ages = ['10', '20', '30', '40', '50', '60']
            age_vals = []
            for a in ages:
                tr = dl.trend([seed], start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'), time_unit='date', ages=[a])
                rs = tr.get('results') or []
                if not rs or not rs[0].get('data'):
                    age_vals.append(0.0)
                else:
                    vals = [x.get('ratio') or 0 for x in rs[0]['data']]
                    age_vals.append(sum(vals)/len(vals) if vals else 0.0)
            st.markdown("**연령대 비중(상대값)**")
            st.bar_chart(pd.DataFrame({"ratio": age_vals}, index=[f"{a}대" for a in ages]))
        except Exception as e:
            st.info(f"인구통계 조회 참고: {e}")

else:
    st.info("왼쪽에서 옵션을 설정하고 **분석 실행 / 갱신**을 눌러주세요.")




