# 天枢 V3 Agents — 惰性导出，避免模块加载期副作用
# P4-4 修复：原 `from orchestrator import ...` 裸导入要求 agents/ 目录在 sys.path 上，
# 导致 `python -c 'import agents'` 直接 ModuleNotFoundError。
# 改为 __getattr__ 惰性 + 相对导入，兼容 main.py 生产路径（内部 sys.path.insert 后照旧工作）。
from __future__ import annotations

__all__ = ["Orchestrator", "NewsAgent", "ScreenAgent", "ReviewAgent", "DecisionAgent", "MarketAgent"]

# 内部 Agent 仍用裸导入（如 base_agent.py `from logger import plog`），需要 agents/ 在 sys.path。
# 在 __init__.py 加载时提前把本目录加进 sys.path，确保任何入口（pytest/Jupyter/子脚本）都能找到兄弟模块。
import sys as _sys
from pathlib import Path as _Path
_AGENTS_DIR = str(_Path(__file__).resolve().parent)
if _AGENTS_DIR not in _sys.path:
    _sys.path.insert(0, _AGENTS_DIR)


def __getattr__(name: str):
    """按需导入 Agent 类，避免顶层循环依赖与副作用。"""
    _MAP = {
        "Orchestrator": "orchestrator",
        "NewsAgent": "news_agent",
        "ScreenAgent": "screen_agent",
        "ReviewAgent": "review_agent",
        "DecisionAgent": "decision_agent",
        "MarketAgent": "market_agent",
    }
    if name in _MAP:
        import importlib
        mod = importlib.import_module(f".{_MAP[name]}", package=__name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
