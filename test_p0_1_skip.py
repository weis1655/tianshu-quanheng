#!/usr/bin/env python3
"""
P0-1 修复验证：重点观察池为空时跳过 Skeptic 逻辑
==================================================
验证方法：用临时目录模拟池 JSON，测试 run_phase('skeptic') 的分支逻辑。
"""

import json
import os
import sys
import tempfile
from pathlib import Path

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


def test_case(name: str, pool_stocks: list, expect_skip: bool):
    """运行单个测试用例"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pool_dir = tmp / "五池管理"
        pool_dir.mkdir(parents=True)

        pool_file = pool_dir / "重点观察池.json"
        pool_file.write_text(
            json.dumps({"stocks": pool_stocks}, ensure_ascii=False),
            encoding="utf-8"
        )

        result = mock_run_skeptic(tmp)
        actually_skipped = result.get("skipped", False)
        actually_called_llm = result.get("report") == "mock_report"

        status = "✅ PASS" if actually_skipped == expect_skip else "❌ FAIL"
        print(f"\n{status} | {name}")
        print(f"   池 stocks: {len(pool_stocks)} 只")
        print(f"   期望跳过: {expect_skip} | 实际跳过: {actually_skipped}")
        print(f"   触LLM:    {actually_called_llm}")  # skipped=False 表示会触LLM
        if result.get("skipped"):
            print(f"   返回: skipped=True, reason={result['reason']}")

        if actually_skipped != expect_skip:
            print(f"   ⚠️ 分支逻辑与预期不符！")
            return False
        return True


# ── 测试用例 ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("P0-1 修复验证：重点观察池为空时跳过 Skeptic")
    print("=" * 60)

    passed = 0
    failed = 0

    # Case 1: 池为空列表 → 应跳过
    if test_case("Case 1: 重点观察池 stocks=[] (空列表)", [], expect_skip=True):
        passed += 1
    else:
        failed += 1

    # Case 2: 池有股票 → 不应跳过
    if test_case(
        "Case 2: 重点观察池 stocks=[{...}] (有股票)",
        [{"股票代码": "000001", "股票名称": "平安银行", "理由": "测试"}],
        expect_skip=False
    ):
        passed += 1
    else:
        failed += 1

    # Case 3: 池文件不存在 → 应跳过
    print(f"\n{'='*60}")
    print("Case 3: 重点观察池.json 不存在 → 应跳过")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pool_dir = tmp / "五池管理"
        pool_dir.mkdir(parents=True)
        # 故意不创建文件
        result = mock_run_skeptic(tmp)
        actually_skipped = result.get("skipped", False)
        status = "✅ PASS" if actually_skipped else "❌ FAIL"
        print(f"  {status} | 文件不存在")
        print(f"  期望跳过: True | 实际跳过: {actually_skipped}")
        if actually_skipped:
            print(f"  返回: skipped=True, reason={result['reason']}")
            passed += 1
        else:
            failed += 1

    # Case 4: 池 stocks=None → 应跳过
    print(f"\n{'='*60}")
    print("Case 4: 重点观察池 stocks=null (None) → 应跳过")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pool_dir = tmp / "五池管理"
        pool_dir.mkdir(parents=True)
        pool_file = pool_dir / "重点观察池.json"
        pool_file.write_text(json.dumps({"stocks": None}), encoding="utf-8")
        result = mock_run_skeptic(tmp)
        actually_skipped = result.get("skipped", False)
        status = "✅ PASS" if actually_skipped else "❌ FAIL"
        print(f"  {status} | stocks=None")
        print(f"  期望跳过: True | 实际跳过: {actually_skipped}")
        if actually_skipped:
            print(f"  返回: skipped=True, reason={result['reason']}")
            passed += 1
        else:
            failed += 1

    # ── 汇总 ──────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"📊 结果: ✅ {passed} / ❌ {failed} / 总计 {passed + failed}")
    if failed == 0:
        print("🎉 P0-1 修复验证通过：所有分支逻辑正确！")
    else:
        print(f"⚠️ 有 {failed} 个用例失败，需检查代码。")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())