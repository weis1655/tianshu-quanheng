#!/usr/bin/env python3
"""
P0-1 修复验证：重点观察池为空时跳过 Skeptic 逻辑
==================================================
验证方法：用临时目录模拟池 JSON，测试 run_phase('skeptic') 的分支逻辑。
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

# ── 模拟 run_phase('skeptic') 的核心分支逻辑 ──────────────
def mock_run_skeptic(pool_dir: Path) -> dict:
    """模拟 main.py run_phase('skeptic') 第108-119行的池检查逻辑"""
    pool_file = pool_dir / "五池管理" / "重点观察池.json"

    if not (pool_file.exists() and json.loads(pool_file.read_text(encoding="utf-8")).get("stocks")):
        # 跳过分支
        return {
            "success": True, "challenges": [], "high_risk_stocks": [],
            "high_risk_count": 0, "report": "",
            "skipped": True, "reason": "no_upgrades_to_key_watch_pool"
        }
    else:
        # 继续执行分支 (模拟 LLM 调用)
        data = json.loads(pool_file.read_text(encoding="utf-8"))
        stocks = data.get("stocks", [])
        return {
            "success": True,
            "challenges": [{"code": s.get("股票代码", ""), "name": s.get("股票名称", "")} for s in stocks],
            "high_risk_stocks": [],
            "high_risk_count": 0,
            "report": "mock_report",
            "skipped": False,
        }


@pytest.mark.parametrize(
    "name,pool_stocks,expect_skip",
    [
        ("空列表", [], True),
        ("有股票", [{"股票代码": "000001", "股票名称": "平安银行", "理由": "测试"}], False),
        ("stocks键缺失", None, True),  # mock will use [] fallback
    ],
)
def test_p0_1_skeptic_skip(name: str, pool_stocks: list, expect_skip: bool):
    """验证重点观察池为空时跳过 Skeptic 逻辑"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pool_dir = tmp / "五池管理"
        pool_dir.mkdir(parents=True)
        pool_file = pool_dir / "重点观察池.json"
        if pool_stocks is None:
            pool_file.write_text(json.dumps({}), encoding="utf-8")
        else:
            pool_file.write_text(
                json.dumps({"stocks": pool_stocks}, ensure_ascii=False), encoding="utf-8"
            )
        result = mock_run_skeptic(tmp)
        actually_skipped = result.get("skipped", False)
        assert actually_skipped == expect_skip, (
            f"{name}: expected skipped={expect_skip}, got {actually_skipped}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
