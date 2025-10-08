from __future__ import annotations

import re
from typing import Iterable, List, Set

_WS_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\t\r\n\v\f]+")


def normalize_query(q: str) -> str:
    q = q.strip()
    q = _CTRL_RE.sub(" ", q)
    q = _WS_RE.sub(" ", q)
    return q


def tokenize(q: str) -> List[str]:
    return [t for t in normalize_query(q).split(" ") if t]


def unique_ordered(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out

