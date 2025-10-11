"""Streamlit entrypoint (repo root).

Robust loader that ensures `src/` is on sys.path and imports
`blog_keyword_analyzer.streamlit_api_only:main`. Falls back to
importing the module by file path if package import fails.
"""
from __future__ import annotations

import os
import sys
import types
import importlib.util
from typing import Callable, Optional


def _src_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "src")


def _ensure_src_on_path() -> None:
    src = _src_dir()
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


def _load_app_main_via_spec(mod_basename: str) -> Callable[[], int | None]:
    src = _src_dir()
    pkg_name = "blog_keyword_analyzer"
    mod_name = f"{pkg_name}.{mod_basename}"
    pkg_path = os.path.join(src, pkg_name)
    file_path = os.path.join(pkg_path, f"{mod_basename}.py")

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

def _cli_mode(argv: list[str]) -> Optional[str]:
    try:
        if "--mode" in argv:
            i = argv.index("--mode")
            if i + 1 < len(argv):
                return argv[i + 1]
        for a in argv:
            if a.startswith("--mode="):
                return a.split("=", 1)[1]
    except Exception:
        pass
    return None

mode = (_cli_mode(sys.argv) or os.getenv("APP_MODE", "monetization")).strip().lower()
aliases = {"money": "monetization", "monetize": "monetization", "plat": "platform"}
mode = aliases.get(mode, mode)
target = {
    "monetization": "streamlit_monetization",
    "platform": "streamlit_platform",
    "api": "streamlit_api_only",
}.get(mode, "streamlit_monetization")
try:
    if target == "streamlit_monetization":
        from blog_keyword_analyzer.streamlit_monetization import main  # type: ignore  # noqa: E402
    elif target == "streamlit_platform":
        from blog_keyword_analyzer.streamlit_platform import main  # type: ignore  # noqa: E402
    else:
        from blog_keyword_analyzer.streamlit_api_only import main  # type: ignore  # noqa: E402
except Exception:
    main = _load_app_main_via_spec(target)

if __name__ == "__main__":
    # Streamlit executes this file as a script; calling main starts the app
    main()
