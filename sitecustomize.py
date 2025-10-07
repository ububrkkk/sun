"""Ensure src/ is importable in all environments (e.g., Streamlit Cloud).

Python automatically imports sitecustomize if it is importable on sys.path.
We use this to add the project "src" directory to sys.path so that
"blog_keyword_analyzer" can be imported reliably without extra setup.
"""
from __future__ import annotations

import os
import sys


def _ensure_src_on_path() -> None:
    root = os.path.dirname(__file__)
    src = os.path.join(root, "src")
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


_ensure_src_on_path()

