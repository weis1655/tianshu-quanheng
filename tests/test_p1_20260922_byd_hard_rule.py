#!/usr/bin/env python3
"""
P1-2026-09-22 回归测试：比亚迪(002594) ML降级边缘池后不应被硬规则校验误杀。

场景：
1. 审查分76（LLM评分）
2. ML评分43（严重背离）→ ReviewAgent降级至边缘池
3. Skeptic阻断 → 从 score d_stocks 中移除
4. Decision LLM 仍输出「主推」执行方案
5. _parse_decision_result_v2 内部硬规则校验时 next() 找不到 s → 旧代码读 score=0 → 误判禁入
6. 修复后：从边缘池回退取到 76 分 → 保守放行

用真实代码路径：构造 ExecutionPlan + 模拟 scored_stocks + patch pool_manager.load_pool
"""
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

from schemas import ExecutionPlan
from thresholds import SCORE_C_LEVEL


def _make_plan():
    """构造最小可运行的 ExecutionPlan（比亚迪）"""
    return ExecutionPlan(
        code="002594",
        name="比亚迪",
        priority="主推",
        pool_position="边缘池",
        driver="新能源车龙头",
        logic="出海加速",
        tech_shape="多头排列",
        index_env="震荡",
        position_pct=5.0,
        buy_method="回调买入",
        trigger_price=86.0,
        stop_loss=80.0,
        stop_loss_pct=6.0,
        target_1_price=92.0,
        target_1_pct=7.0,
        target_1_action="卖1/2",
        target_2_price=98.0,
        target_2_pct=14.0,
        target_2_action="清仓",
        invalid_condition="跌破80止损",
        invalid_price=80.0,
        no_go_rules=["跌破止损", "大盘跳水", "基本面变化"],
        risk_notes=["ML低信心"],
        hypothesis="",
        expected_logic="",
    )


class _FakePoolManager:
    """模拟 PoolManager：边缘池有比亚迪 76 分，S池综合评分为 null"""
    def __init__(self):
        self.edge_stocks = [
            {"代码": "002594", "名称": "比亚迪", "综合分": 76, "降级时间": "2026-09-22"},
        ]
        self.s_stocks = [
            {"代码": "002594", "名称": "比亚迪", "综合评分": None, "纳入日期": "2026-09-22"},
        ]

    def load_pool(self, pool_name):
        if pool_name == "边缘池":
            return {"stocks": self.edge_stocks}
        if pool_name == "S级操作池":
            return {"stocks": self.s_stocks}
        return {"stocks": []}


def test_byd_ml_downgrade_not_killed_by_hard_rule():
    """P1-2026-09-22：比亚迪ML降级→边缘池→被Skeptic移除后，硬规则校验不应误杀"""
    from decision_agent import DecisionAgent

    da = DecisionAgent.__new__(DecisionAgent)
    da.pool_manager = _FakePoolManager()

    # 模拟 score d_stocks：Skeptic 已移除比亚迪，只剩别的股票
    scored_stocks = [
        {"code": "600519", "name": "贵州茅台", "composite_score": 80, "score": 80, "passed": True},
    ]

    plan = _make_plan()

    # 直接调用 _parse_decision_result_v2 的内部硬规则路径会返回 DecisionResult；
    # 我们只跑一段等价的、与 L1376-1430 相同逻辑的最小验证：
    # 但为了走真实代码，用完整 _parse_decision_result_v2。
    raw = """
### 【主推】比亚迪（002594）
单笔仓位：5%
买入方式：回调买入
触发条件：86.00元
止损：80.00元
第一目标价：92.00元
第二目标价：98.00元
失效条件：跌破80止损
失效触发价：80.00元
核心驱动：新能源车龙头
逻辑支撑：出海加速
技术形态：多头排列
指数环境：震荡
池子位置：边缘池
不做的情况：
- 跌破止损
- 大盘跳水
- 基本面变化
风险提示：
- ML低信心
"""
    result = da._parse_decision_result_v2(raw, scored_stocks)

    # 关键断言：plans 里必须包含比亚迪 —— 不被硬规则误杀
    codes = [p.code for p in result.plans]
    assert "002594" in codes, (
        f"比亚迪应被保守放行（边缘池综合分=76），但硬规则误杀，plans={codes}"
    )
    print("✅ P1修复：比亚迪ML降级边缘池→硬规则未误杀 (边缘池回退76分)")
    print(f"   SCORE_C_LEVEL={SCORE_C_LEVEL}, plans={codes}")


def test_low_score_still_killed_when_scored_stocks_present():
    """回归保护：scored_stocks 里存在且真实评分<55，仍应被移除"""
    from decision_agent import DecisionAgent

    da = DecisionAgent.__new__(DecisionAgent)
    da.pool_manager = _FakePoolManager()

    # score d_stocks 里有真实低分股票（非缺失场景）
    scored_stocks = [
        {"code": "000001", "name": "平安银行", "composite_score": 50, "score": 50, "passed": False},
    ]

    plan = ExecutionPlan(
        code="000001", name="平安银行", priority="主推", pool_position="候选池",
        driver="x", logic="x", tech_shape="x", index_env="x",
        position_pct=5.0, buy_method="x",
        trigger_price=10.0, stop_loss=9.0, stop_loss_pct=10.0,
        target_1_price=11.0, target_1_pct=10.0, target_1_action="卖1/2",
        target_2_price=12.0, target_2_pct=20.0, target_2_action="清仓",
        invalid_condition="x", invalid_price=9.0,
    )

    raw = """
### 【主推】平安银行（000001）
单笔仓位：5%
买入方式：x
触发条件：10.00元
止损：9.00元
第一目标价：11.00元
第二目标价：12.00元
失效条件：x
失效触发价：9.00元
核心驱动：x
逻辑支撑：x
技术形态：x
指数环境：x
池子位置：候选池
不做的情况：
- x
- x
- x
风险提示：
- x
"""
    result = da._parse_decision_result_v2(raw, scored_stocks)

    codes = [p.code for p in result.plans]
    assert "000001" not in codes, "真实评分50<55应被硬规则移除"
    print("✅ 回归保护：真实低分(50)仍被正确移除")


def test_score_missing_and_pool_empty_is_warned_not_killed():
    """所有源都无评分：plog WARNING 保守放行（不再移除）"""
    from decision_agent import DecisionAgent

    class _EmptyPM:
        def load_pool(self, _name):
            return {"stocks": []}

    da = DecisionAgent.__new__(DecisionAgent)
    da.pool_manager = _EmptyPM()

    scored_stocks = []  # 完全缺失

    plan = ExecutionPlan(
        code="999999", name="幽灵股", priority="主推", pool_position="未知",
        driver="x", logic="x", tech_shape="x", index_env="x",
        position_pct=5.0, buy_method="x",
        trigger_price=10.0, stop_loss=9.0, stop_loss_pct=10.0,
        target_1_price=11.0, target_1_pct=10.0, target_1_action="卖1/2",
        target_2_price=12.0, target_2_pct=20.0, target_2_action="清仓",
        invalid_condition="x", invalid_price=9.0,
    )

    raw = """
### 【主推】幽灵股（999999）
单笔仓位：5%
买入方式：x
触发条件：10.00元
止损：9.00元
第一目标价：11.00元
第二目标价：12.00元
失效条件：x
失效触发价：9.00元
核心驱动：x
逻辑支撑：x
技术形态：x
指数环境：x
池子位置：未知
不做的情况：
- x
- x
- x
风险提示：
- x
"""
    result = da._parse_decision_result_v2(raw, scored_stocks)

    codes = [p.code for p in result.plans]
    assert "999999" in codes, "评分源全缺失应保守放行(WARNING)而非移除"
    print("✅ 全源缺失→保守放行（WARNING，不移除）")


if __name__ == "__main__":
    test_byd_ml_downgrade_not_killed_by_hard_rule()
    test_low_score_still_killed_when_scored_stocks_present()
    test_score_missing_and_pool_empty_is_warned_not_killed()
    print("\n🎉 P1-2026-09-22 全部回归测试通过")
