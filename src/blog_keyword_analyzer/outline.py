from __future__ import annotations

from typing import Dict, List

from .text_utils import tokenize


def build_outline(keyword: str) -> Dict[str, List[str]]:
    toks = tokenize(keyword)
    head = keyword
    title = f"{head} 총정리"
    sections = [
        f"{head} 한눈에 보기",
        f"{head} 핵심 체크리스트",
        f"{head} 자주 묻는 질문",
        f"{head} 비교/대안",
        f"{head} 최종 선택 가이드",
    ]
    faq = [
        f"Q. {head} 초보도 가능한가요?",
        f"Q. {head} 비용/가격 팁은?",
        f"Q. {head} 주의할 점은?",
        f"Q. {head} 대체 키워드는?",
    ]
    return {"title": [title], "sections": sections, "faq": faq}

