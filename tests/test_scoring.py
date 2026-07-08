#!/usr/bin/env python3
"""
WO-103 单元测试1: 评分计算链路
测试内容：
1. OverheatDetector 过热检测 — 6条规则
2. thresholds.score_to_level() 等级转换
3. review_agent 的评分提取逻辑
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))

from thresholds import score_to_level, SCORE_S_LEVEL, SCORE_A_LEVEL, SCORE_B_LEVEL, SCORE_C_LEVEL
from thresholds import AUTO_DOWNGRADE_SCORE, HARD_DOWNGRADE_SCORE

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
# 测试1: score_to_level 等级转换
# ──────────────────────────────────────────────
def test_score_to_level():
    print("\n📋 测试1: score_to_level 等级转换")
    check("S级: 100分", score_to_level(100) == "S级")
    check("S级: 90分", score_to_level(SCORE_S_LEVEL) == "S级")
    check("A级: 89分", score_to_level(SCORE_S_LEVEL - 1) == "A级")
    check("A级: 75分", score_to_level(SCORE_A_LEVEL) == "A级")
    check("B级: 74分", score_to_level(SCORE_A_LEVEL - 1) == "B级(黄色预警)")
    check("B级: 65分", score_to_level(SCORE_B_LEVEL) == "B级(黄色预警)")
    check("C级: 64分", score_to_level(SCORE_B_LEVEL - 1) == "C级(观察区)")
    check("C级: 55分", score_to_level(SCORE_C_LEVEL) == "C级(观察区)")
    check("D级: 54分", score_to_level(SCORE_C_LEVEL - 1) == "D级(淘汰)")
    check("D级: 0分", score_to_level(0) == "D级(淘汰)")
    check("升序一致性: 100→0 不降级", score_to_level(100) != score_to_level(0))
    check("阈值边界: A级75分", score_to_level(75) == "A级")
    check("阈值边界: B级65分", score_to_level(65) == "B级(黄色预警)")
    check("阈值边界: C级55分", score_to_level(55) == "C级(观察区)")

# ──────────────────────────────────────────────
# 测试2: OverheatDetector 过热检测
# ──────────────────────────────────────────────
def test_overheat_detector():
    print("\n📋 测试2: OverheatDetector 过热检测")
    from review_scorer import OverheatDetector

    # RULE 1: CRITICAL — 日涨幅>12% + PE>80 + 换手>12%
    result = OverheatDetector.detect(
        change_pct=14.0, pe_ttm=100, turnover=15.0,
        volume_ratio=1.5, month_chg=5.0, quarter_chg=10.0,
        composite_score=75, amplitude=3.0,
    )
    check("RULE1 CRITICAL: 日涨12%+PE80+换手12%", result and result["overheat_level"] == "critical",
          f"got={result}")

    # RULE 2: CRITICAL — 月涨>25% + 评分>=70
    result = OverheatDetector.detect(
        change_pct=3.0, pe_ttm=30, turnover=3.0,
        volume_ratio=1.0, month_chg=30.0, quarter_chg=40.0,
        composite_score=75, amplitude=2.0,
    )
    check("RULE2 CRITICAL: 月涨30%+评分75", result and result["overheat_level"] == "critical",
          f"got={result}")

    # RULE 3: CRITICAL — 季涨>50%
    result = OverheatDetector.detect(
        change_pct=3.0, pe_ttm=30, turnover=3.0,
        volume_ratio=1.0, month_chg=10.0, quarter_chg=60.0,
        composite_score=75, amplitude=2.0,
    )
    check("RULE3 CRITICAL: 季涨60%", result and result["overheat_level"] == "critical",
          f"got={result}")

    # RULE 4: WARNING-1 — 日涨>8% + 评分>75 (非强市)
    result = OverheatDetector.detect(
        change_pct=10.0, pe_ttm=30, turnover=5.0,
        volume_ratio=1.0, month_chg=5.0, quarter_chg=10.0,
        composite_score=80, amplitude=2.0,
    )
    check("RULE4 WARNING: 日涨10%+评分80", result and result["overheat_level"] == "warning",
          f"got={result}")

    # RULE 5: WARNING-2 — 涨幅>10% + 评分>70
    result = OverheatDetector.detect(
        change_pct=12.0, pe_ttm=30, turnover=5.0,
        volume_ratio=1.0, month_chg=5.0, quarter_chg=10.0,
        composite_score=75, amplitude=2.0,
    )
    check("RULE5 WARNING: 日涨12%+评分75", result and result["overheat_level"] == "warning",
          f"got={result}")

    # 正常标的：无过热
    result = OverheatDetector.detect(
        change_pct=2.0, pe_ttm=20, turnover=3.0,
        volume_ratio=1.0, month_chg=3.0, quarter_chg=5.0,
        composite_score=75, amplitude=2.0,
    )
    check("正常标的: 无过热", result is None, f"got={result}")

    # 强市豁免：WARNING 涨幅>8%但强市（通过 market_state 判断）
    result = OverheatDetector.detect(
        change_pct=10.0, pe_ttm=30, turnover=5.0,
        volume_ratio=1.0, month_chg=5.0, quarter_chg=10.0,
        composite_score=80, amplitude=2.0, market_state="偏多",
    )
    check("强市豁免: 日涨10%+评分80+偏多市场→不过热",
          result is None or result["overheat_level"] != "warning",
          f"got={result}")

# ──────────────────────────────────────────────
# 测试3: 阈值常量一致性
# ──────────────────────────────────────────────
def test_threshold_consistency():
    print("\n📋 测试3: 阈值常量一致性")
    # 等级递进验证
    check("S级>A级", SCORE_S_LEVEL > SCORE_A_LEVEL)
    check("A级>B级", SCORE_A_LEVEL > SCORE_B_LEVEL)
    check("B级>C级", SCORE_B_LEVEL > SCORE_C_LEVEL)
    # 降级阈值关系
    check("硬性降级<自动降级", HARD_DOWNGRADE_SCORE < AUTO_DOWNGRADE_SCORE,
          f"hard={HARD_DOWNGRADE_SCORE} auto={AUTO_DOWNGRADE_SCORE}")
    # 区间连续性
    check("A级(75) > B级(65) 差10分", SCORE_A_LEVEL - SCORE_B_LEVEL == 10,
          f"diff={SCORE_A_LEVEL - SCORE_B_LEVEL}")
    check("B级(65) > C级(55) 差10分", SCORE_B_LEVEL - SCORE_C_LEVEL == 10,
          f"diff={SCORE_B_LEVEL - SCORE_C_LEVEL}")

# ──────────────────────────────────────────────
# 运行
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("📊 单元测试1: 评分计算链路")
    print("=" * 50)
    test_score_to_level()
    test_overheat_detector()
    test_threshold_consistency()
    print(f"\n{'='*50}")
    print(f"🏁 {TOTAL} 项测试, {PASS} PASS, {FAIL} FAIL")
    print(f"{'='*50}")
    sys.exit(0 if FAIL == 0 else 1)