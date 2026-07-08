#!/usr/bin/env python3
"""
F10: 池清理模块 — 从 pool_manager.py 提取的独立职责
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def clean_expired_candidates(pool_dir: Path, pm, max_age_days: int = 14) -> dict:
    """清理候选池中超过 max_age_days 未升级的标的"""
    return pm.clean_expired_candidates(max_age_days=max_age_days)


def clean_expired_edge_pool(pool_dir: Path, pm, max_age_days: int = 45, min_score: float = 40) -> dict:
    """清理边缘池中过期或低评分的标的"""
    return pm.clean_expired_edge_pool(max_age_days=max_age_days, min_score=min_score)


def sweep_low_score_stocks(pool_dir: Path, pm) -> dict:
    """扫描全部池中低分标的（委托 sweep_downgrade）"""
    from scripts.sweep_downgrade import sweep_all_pools
    return sweep_all_pools(pm)


def clean_expired_s_pool(pool_dir: Path, pm, max_age_days: int = 1) -> dict:
    """清理S级操作池中期满的标的"""
    return pm.clean_expired_s_pool(max_age_days=max_age_days, pool_dir=str(pool_dir))


if __name__ == "__main__":
    from pool_manager import PoolManager
    pm = PoolManager()
    pool_dir = pm.pool_dir
    print(f"🧹 池清理工具 (F10)")
    print(f"   候选池清理: {clean_expired_candidates(pool_dir, pm)['removed_count']} 只")
    print(f"   边缘池清理: {clean_expired_edge_pool(pool_dir, pm)['removed_count']} 只")
    print(f"   sweep扫描: {sweep_low_score_stocks(pool_dir, pm)['total_demoted']} 只")
    print(f"✅ F10: pool_cleanup 独立模块就绪")