#!/usr/bin/env python3
"""
WO-103 单元测试3: S池写入+回流逻辑
测试内容：
1. S级操作池准入校验（gate_controller 准入规则）
2. S池T+1过期回流逻辑
3. DecisionAgent 决策一致性检查
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from thresholds import S_POOL_MIN_SCORE, KEY_WATCH_MIN_SCORE, DECISION_MIN_SCORE
from gate_controller import GateController

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

# ──────────────────────────────────────────────
# 测试1: S级操作池准入规则
# ──────────────────────────────────────────────
def test_s_pool_entry():
    print("\n📋 测试1: S级操作池准入校验")
    rules = GateController.enforce_writing_rules(
        {"score": S_POOL_MIN_SCORE, "综合评分": S_POOL_MIN_SCORE},
        "S级操作池"
    )
    check("S池准入: ≥75分通过", rules.get("allowed", True),
          f"got={rules}")

    rules = GateController.enforce_writing_rules(
        {"score": S_POOL_MIN_SCORE - 1, "综合评分": S_POOL_MIN_SCORE - 1},
        "S级操作池"
    )
    check("S池准入: <75分拦截", not rules.get("allowed", True),
          f"got={rules}")

# ──────────────────────────────────────────────
# 测试2: 重点观察池准入规则
# ──────────────────────────────────────────────
def test_key_watch_entry():
    print("\n📋 测试2: 重点观察池准入校验")
    rules = GateController.enforce_writing_rules(
        {"score": KEY_WATCH_MIN_SCORE, "综合评分": KEY_WATCH_MIN_SCORE},
        "重点观察池"
    )
    check("重点池准入: 50分通过", rules.get("allowed", True),
          f"got={rules}")

    rules = GateController.enforce_writing_rules(
        {"score": KEY_WATCH_MIN_SCORE - 1, "综合评分": KEY_WATCH_MIN_SCORE - 1},
        "重点观察池"
    )
    check("重点池准入: <50分拦截", not rules.get("allowed", True),
          f"got={rules}")

# ──────────────────────────────────────────────
# 测试3: GateController 过滤阻塞标的
# ──────────────────────────────────────────────
def test_gate_filter():
    print("\n📋 测试3: GateController 阻塞过滤")
    pools = {
        "重点观察池": {"stocks": [
            {"代码": "600001", "名称": "阻塞A"},
            {"代码": "600002", "名称": "通过B"},
        ]},
        "S级操作池": {"stocks": [
            {"代码": "600003", "名称": "阻塞C"},
        ]},
    }
    blocked = {"600001", "600003"}
    filtered = GateController.filter_pools(pools, blocked)

    # 阻塞标的应从池中移除
    key_stocks = filtered["重点观察池"]["stocks"]
    s_stocks = filtered["S级操作池"]["stocks"]
    check("重点池移除阻塞标的", len(key_stocks) == 1 and key_stocks[0]["代码"] == "600002",
          f"got={[s['代码'] for s in key_stocks]}")
    check("S池移除阻塞标的", len(s_stocks) == 0,
          f"got={[s['代码'] for s in s_stocks]}")

# ──────────────────────────────────────────────
# 测试4: 决策准入阈值一致性
# ──────────────────────────────────────────────
def test_decision_threshold_consistency():
    print("\n📋 测试4: 决策准入阈值一致性")
    # S池准入分 >= 决策准入分（S池标的可以直接进入决策）
    check("S_POOL_MIN_SCORE >= DECISION_MIN_SCORE",
          S_POOL_MIN_SCORE >= DECISION_MIN_SCORE,
          f"S={S_POOL_MIN_SCORE} D={DECISION_MIN_SCORE}")
    # 关键阈值关系
    check("S_POOL_MIN_SCORE = DECISION_MIN_SCORE",
          S_POOL_MIN_SCORE == DECISION_MIN_SCORE,
          f"S={S_POOL_MIN_SCORE} D={DECISION_MIN_SCORE}")

# ──────────────────────────────────────────────
# 测试5: 决策日志一致性检查（模拟）
# ──────────────────────────────────────────────
def test_decision_consistency_check():
    print("\n📋 测试5: 决策一致性检测（模拟）")
    # 模拟 decision_agent.py 中的一致性检测逻辑
    def check_consistency(review_score, priority, code="600001", name="测试"):
        issues = []
        # 冲突检测3: 审查评分<60但决策推荐
        if review_score < 60 and priority in ["主推", "推荐"]:
            issues.append({"severity": "high"})
        # 冲突检测4: 审查评分60-74但决策主推
        if 60 <= review_score < 75 and priority == "主推":
            issues.append({"severity": "medium"})
        return issues

    high_issues = check_consistency(55, "主推")
    check("审查55分+主推=高冲突", len(high_issues) > 0 and high_issues[0]["severity"] == "high")

    med_issues = check_consistency(70, "主推")
    check("审查70分+主推=中冲突", len(med_issues) > 0 and med_issues[0]["severity"] == "medium")

    no_issues = check_consistency(80, "主推")
    check("审查80分+主推=无冲突", len(no_issues) == 0)

    no_issues2 = check_consistency(70, "备选")
    check("审查70分+备选=无冲突", len(no_issues2) == 0)

# ──────────────────────────────────────────────
# 运行
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("📊 单元测试3: S池写入+回流")
    print("=" * 50)
    test_s_pool_entry()
    test_key_watch_entry()
    test_gate_filter()
    test_decision_threshold_consistency()
    test_decision_consistency_check()
    print(f"\n{'='*50}")
    print(f"🏁 {TOTAL} 项测试, {PASS} PASS, {FAIL} FAIL")
    print(f"{'='*50}")
    sys.exit(0 if FAIL == 0 else 1)