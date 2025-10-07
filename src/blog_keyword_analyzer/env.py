from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def load_env(filename: str = ".env", search_from: Optional[str] = None) -> None:
    """Load environment variables from .env and Streamlit secrets.

    - .env: best-effort using python-dotenv if available.
    - Streamlit secrets: if `streamlit` is available and `st.secrets` is populated,
      copy values into os.environ if not already set.
    """
    # .env
    try:
        from dotenv import load_dotenv  # type: ignore

        start = Path(search_from or os.getcwd()).resolve()
        candidates = [start]
        for _ in range(3):
            if candidates[-1].parent == candidates[-1]:
                break
            candidates.append(candidates[-1].parent)
        for base in candidates:
            env_path = base / filename
            if env_path.exists():
                load_dotenv(dotenv_path=str(env_path), override=False)
                break
    except Exception:
        pass

    # Streamlit secrets
    try:
        import streamlit as st  # type: ignore

        def _flatten(prefix: str, obj) -> dict[str, str]:
            flat: dict[str, str] = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    flat.update(_flatten(f"{prefix}{k}." if prefix else k + ".", v))
            else:
                key = prefix[:-1] if prefix.endswith(".") else prefix
                flat[key] = str(obj)
            return flat

        if hasattr(st, "secrets") and st.secrets:  # type: ignore[attr-defined]
            if isinstance(st.secrets, dict):  # type: ignore
                entries = _flatten("", dict(st.secrets))  # type: ignore
                for k, v in entries.items():
                    if k.isupper() and k not in os.environ:
                        os.environ[k] = v
                    if "." in k:
                        _, last = k.rsplit(".", 1)
                        if last.isupper() and last not in os.environ:
                            os.environ[last] = v
    except Exception:
        pass

