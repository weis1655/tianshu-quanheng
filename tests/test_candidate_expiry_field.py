#!/usr/bin/env python3
"""
WO-20260911 回归测试: clean_expired_candidates 字段名漂移

背景（2026-09-11 盘中复盘发现）：
- screen_agent.py:481 写候选池标的时用字段名「纳入日期」
- pool_manager.py:434 clean_expired_candidates 却只读「入池日期」
- 全库无任何代码写「入池日期」到候选池，导致 s.get("入池日期") 恒为 None
- 恒返回兜底值 "2000-01-01" < cutoff，于是【所有候选标的每轮都被判为超期删除】
- 后果：ScreenAgent 10:52 写入6只 → PoolManager 10:56 全部清空，快筛持续空转
  （统计字段显示 累计进入=6 但 持仓数=0，日志连续多日出现「移除N只超期标的」）

修复：双读兼容 s.get("入池日期") or s.get("纳入日期")，与 screen_agent/
weekly_review_agent 实际写入的字段对齐。

复现脚本 /tmp/repro_pool_clear.py 已验证：修复前 6只→保留0只；修复后 6只→保留6只。
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from pool_manager import PoolManager


def _today(days_offset=0):
    return (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")


def test_today_added_kept_with_narude_rqi():
    """当日新增、字段名为「纳入日期」的标的，不得被超期清理删除。"""
    pm = PoolManager()
    tmpdir = tempfile.mkdtemp()
    try:
        pool_path = pm.get_pool_path("快筛候选池")
        orig = Path(pool_path).read_text(encoding="utf-8")
        try:
            stocks = [
                {"代码": "600967", "名称": "内蒙一机", "纳入日期": _today(), "综合分": 72},
                {"代码": "600184", "名称": "光电股份", "纳入日期": _today(), "综合分": 68},
                {"代码": "688047", "名称": "龙芯中科", "纳入日期": _today(), "综合分": 61},
                {"代码": "301236", "名称": "软通动力", "纳入日期": _today(), "综合分": 58},
                {"代码": "601186", "名称": "中国铁建", "纳入日期": _today(), "综合分": 55},
                {"代码": "601800", "名称": "中国交建", "纳入日期": _today(), "综合分": 52},
            ]
            Path(pool_path).write_text(
                json.dumps({"stocks": stocks, "统计": {"持仓数": len(stocks)}},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            result = pm.clean_expired_candidates(max_age_days=14)
            assert result["removed_count"] == 0, (
                f"当日新增标的被误删 {result['removed_count']} 只（应为0），"
                f"字段名漂移未修复: {result}"
            )
            assert result["total_before"] == 6
        finally:
            Path(pool_path).write_text(orig, encoding="utf-8")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("PASS: test_today_added_kept_with_narude_rqi")


def test_genuinely_expired_still_removed():
    """真正超期（>14天）的标的不受影响，仍应被清理。"""
    pm = PoolManager()
    pool_path = pm.get_pool_path("快筛候选池")
    orig = Path(pool_path).read_text(encoding="utf-8")
    try:
        old = _today(-20)   # 20天前入池
        new = _today()
        stocks = [
            {"代码": "600001", "名称": "过期股A", "纳入日期": old, "综合分": 60},
            {"代码": "600002", "名称": "过期股B", "纳入日期": old, "综合分": 58},
            {"代码": "600003", "名称": "新入池C", "纳入日期": new, "综合分": 70},
        ]
        Path(pool_path).write_text(
            json.dumps({"stocks": stocks, "统计": {"持仓数": 3}}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = pm.clean_expired_candidates(max_age_days=14)
        assert result["removed_count"] == 2, f"应删2只真超期标的，实际 {result['removed_count']}"
        assert result["total_before"] == 3
        remaining = pm.load_pool("快筛候选池").get("stocks", [])
        codes = [s["代码"] for s in remaining]
        assert codes == ["600003"], f"应仅剩新入池标的，实际 {codes}"
        print(f"PASS: test_genuinely_expired_still_removed (removed={result['removed_count']})")
    finally:
        Path(pool_path).write_text(orig, encoding="utf-8")


def test_missing_date_field_removed():
    """完全无日期字段的脏数据仍应被清理，不因本次修复而豁免。"""
    pm = PoolManager()
    pool_path = pm.get_pool_path("快筛候选池")
    orig = Path(pool_path).read_text(encoding="utf-8")
    try:
        stocks = [{"代码": "600009", "名称": "无日期股", "综合分": 66}]
        Path(pool_path).write_text(
            json.dumps({"stocks": stocks, "统计": {"持仓数": 1}}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = pm.clean_expired_candidates(max_age_days=14)
        assert result["removed_count"] == 1, f"无日期字段应删除，实际 {result['removed_count']}"
        print("PASS: test_missing_date_field_removed")
    finally:
        Path(pool_path).write_text(orig, encoding="utf-8")


if __name__ == "__main__":
    test_today_added_kept_with_narude_rqi()
    test_genuinely_expired_still_removed()
    test_missing_date_field_removed()
    print("\n✅ 全部通过：字段名漂移已修复，真超期清理不受影响")
