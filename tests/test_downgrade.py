#!/usr/bin/env python3
"""
WO-103 单元测试2: 降级判断
测试内容：
1. _scan_and_downgrade 低分降级逻辑
2. 评分0分3天强制降级（WO-002）
3. sweep_downgrade.py 独立扫描
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from thresholds import AUTO_DOWNGRADE_SCORE, SCORE_A_LEVEL
from pool_manager import PoolManager

PASS = 0
FAIL = 0
TOTAL = 0

def check(name, condition, detail=""):
    global TOTAL, PASS, FAIL
    TOTAL += 1
    if condition:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name} | {detail}")
        FAIL += 1

def make_test_env():
    """创建测试用的临时池环境"""
    temp_dir = Path(tempfile.mkdtemp())
    pm = PoolManager()
    original_pool_dir = pm.pool_dir
    pm.pool_dir = temp_dir
    temp_dir.mkdir(parents=True, exist_ok=True)
    return pm, temp_dir, original_pool_dir

def cleanup_test_env(temp_dir, pm, original_pool_dir):
    """清理测试环境"""
    pm.pool_dir = original_pool_dir
    shutil.rmtree(temp_dir)

def create_pool_json(temp_dir, pool_name, stocks):
    """创建池JSON文件"""
    pool_file = temp_dir / f"{pool_name}.json"
    data = {
        "池名称": pool_name,
        "stocks": stocks,
        "统计": {"创建日期": "2026-07-01", "持仓数": len(stocks)}
    }
    pool_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return pool_file

# ──────────────────────────────────────────────
# 测试1: _scan_and_downgrade 正常降级
# ──────────────────────────────────────────────
def test_normal_downgrade():
    print("\n📋 测试1: 正常低分降级")
    pm, temp_dir, orig_dir = make_test_env()
    try:
        # 创建重点观察池：混合高分+低分
        today_str = datetime.now().strftime("%Y-%m-%d")
        create_pool_json(temp_dir, "重点观察池", [
            {"代码": "600001", "名称": "高分股A", "综合分": SCORE_A_LEVEL + 5, "纳入日期": today_str},
            {"代码": "600002", "名称": "低分股B", "综合分": AUTO_DOWNGRADE_SCORE - 1, "纳入日期": today_str},
            {"代码": "600003", "名称": "低分股C", "综合分": 30, "纳入日期": today_str},
            {"代码": "600004", "名称": "边界股D", "综合分": AUTO_DOWNGRADE_SCORE, "纳入日期": today_str},
        ])

        data = pm.load_pool("重点观察池")
        demoted = pm._scan_and_downgrade(data)
        check("降级低分股", len(demoted) == 2,
              f"预期2只(<{AUTO_DOWNGRADE_SCORE})，实际{len(demoted)}")
        check("高分股保留", len(data["stocks"]) == 2,
              f"保留{len(data['stocks'])}只")
    finally:
        cleanup_test_env(temp_dir, pm, orig_dir)

# ──────────────────────────────────────────────
# 测试2: 评分0分+3天强制降级
# ──────────────────────────────────────────────
def test_zero_score_downgrade():
    print("\n📋 测试2: 评分0分+入池3天强制降级（WO-002）")
    pm, temp_dir, orig_dir = make_test_env()
    try:
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        one_day_ago = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        today_str = datetime.now().strftime("%Y-%m-%d")

        create_pool_json(temp_dir, "重点观察池", [
            {"代码": "600010", "名称": "零分滞留A", "综合分": 0, "纳入日期": three_days_ago},
            {"代码": "600011", "名称": "零分刚入B", "综合分": 0, "纳入日期": one_day_ago},
            {"代码": "600012", "名称": "零分今日C", "综合分": 0, "纳入日期": today_str},
            {"代码": "600013", "名称": "正常高分D", "综合分": 85, "纳入日期": three_days_ago},
        ])

        data = pm.load_pool("重点观察池")
        demoted = pm._scan_and_downgrade(data)
        check("3天零分强制降级", any("600010" in str(s) for s in demoted),
              f"demoted={[s['代码'] for s in demoted]}")
        check("零分标的自动降级（0<65常规规则）", any("600011" in str(s) for s in demoted),
              f"demoted={[s['代码'] for s in demoted]}")
        check("高分不降级", not any("600013" in str(s) for s in demoted),
              f"demoted={[s['代码'] for s in demoted]}")
    finally:
        cleanup_test_env(temp_dir, pm, orig_dir)

# ──────────────────────────────────────────────
# 测试3: 全池扫描（sweep模式）
# ──────────────────────────────────────────────
def test_sweep_all_pools():
    print("\n📋 测试3: sweep_all_pools 全池扫描")
    from scripts.sweep_downgrade import sweep_all_pools
    pm, temp_dir, orig_dir = make_test_env()
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        # 创建3个测试池，各含不同评分标的
        for pool_name in ["重点观察池", "快筛候选池", "S级操作池"]:
            create_pool_json(temp_dir, pool_name, [
                {"代码": f"9{str(i).zfill(4)}", "名称": f"高分{i}", "综合分": 80, "纳入日期": today_str}
                for i in range(2)
            ] + [
                {"代码": f"5{str(i).zfill(4)}", "名称": f"低分{i}", "综合分": 55, "纳入日期": today_str}
                for i in range(1)
            ])

        report = sweep_all_pools(pm, dry_run=True)
        check("扫描3个池", len(report["scanned_pools"]) == 3)
        check("扫描发现低分标的", report["total_demoted"] > 0,
              f"total_demoted={report['total_demoted']}")
    finally:
        cleanup_test_env(temp_dir, pm, orig_dir)

# ──────────────────────────────────────────────
# 测试4: 边缘池容量限制
# ──────────────────────────────────────────────
def test_edge_pool_capacity():
    print("\n📋 测试4: 边缘池容量限制（30只上限）")
    # 检查 POOL_CAPACITY_LIMITS 中的边缘池容量
    pm = PoolManager()
    cap = pm.POOL_CAPACITY_LIMITS.get("边缘池", 0)
    check("边缘池容量=30", cap == 30, f"实际={cap}")
    check("容量为正整数", cap > 0)

# ──────────────────────────────────────────────
# 运行
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("📊 单元测试2: 降级判断")
    print("=" * 50)
    test_normal_downgrade()
    test_zero_score_downgrade()
    test_sweep_all_pools()
    test_edge_pool_capacity()
    print(f"\n{'='*50}")
    print(f"🏁 {TOTAL} 项测试, {PASS} PASS, {FAIL} FAIL")
    print(f"{'='*50}")
    sys.exit(0 if FAIL == 0 else 1)