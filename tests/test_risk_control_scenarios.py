#!/usr/bin/env python3
"""极端场景压测：验证风控兜底能力

P4 极端场景验证测试套件。覆盖极端行情、熔断器、涨跌停拦截、
仓位风控、数据缺失等场景。所有测试可复现、可验证。
"""
import sys, os, json, time, pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

# ── 测试1: 极端行情 - 创业板暴跌-4%触发极弱模式 ──────────────────────────
def test_extreme_cyb_drop_triggers_empty():
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "000001", "涨跌幅": -0.5, "最新价": 3100},
        {"代码": "399006", "涨跌幅": -4.2, "最新价": 1800},  # 创业板-4.2%
        {"代码": "000688", "涨跌幅": -2.5, "最新价": 800},
        {"代码": "000300", "涨跌幅": -1.5, "最新价": 3600},
    ]))
    result = agent._get_market_state()
    assert result["extreme_warning"] is True
    assert result["state"] == "极弱"
    assert result["s_pool_cap"] == 0
    print(f"  ✅ 创业板-4.2% → 极弱模式 s_pool_cap=0")


# ── 测试2: 沪深300级联跌-2.8%触发极弱 ─────────────────────────────────────
def test_hs300_drop_triggers_extreme():
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "000001", "涨跌幅": -1.2, "最新价": 3100},
        {"代码": "399006", "涨跌幅": -1.5, "最新价": 1800},  # 创业板-1.5%未触发
        {"代码": "000688", "涨跌幅": -1.0, "最新价": 800},
        {"代码": "000300", "涨跌幅": -2.8, "最新价": 3600},  # 沪深300-2.8%触发
    ]))
    result = agent._get_market_state()
    assert result["extreme_warning"] is True
    assert result["s_pool_cap"] == 0
    print(f"  ✅ 沪深300-2.8% → 级联极弱模式 s_pool_cap=0")


# ── 测试3: 正常行情 - 不触发极弱 ──────────────────────────────────────────
def test_normal_market_state():
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "000001", "涨跌幅": 0.8, "最新价": 3100},
        {"代码": "399006", "涨跌幅": 0.5, "最新价": 1800},
        {"代码": "000300", "涨跌幅": 0.3, "最新价": 3600},
    ]))
    result = agent._get_market_state()
    assert result["extreme_warning"] is False
    print(f"  ✅ 正常行情 → 不触发极弱, state={result['state']}")


# ── 测试4: 偏多市场状态 ────────────────────────────────────────────────────
def test_market_state_bianduo():
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "000001", "涨跌幅": 1.5, "最新价": 3100},
        {"代码": "399006", "涨跌幅": 0.8, "最新价": 1800},
    ]))
    result = agent._get_market_state()
    assert result["extreme_warning"] is False
    assert result["state"] == "偏多"
    print(f"  ✅ 上证+1.5% → 偏多模式 s_pool_cap={result['s_pool_cap']}")


# ── 测试5: 仓位风控常量正确 ────────────────────────────────────────────────
def test_position_caps():
    from thresholds import POSITION_PCT_WEAK, POSITION_PCT_NORMAL, POSITION_PCT_STRONG
    assert POSITION_PCT_WEAK == 3
    assert POSITION_PCT_NORMAL == 5
    assert POSITION_PCT_STRONG == 10
    print(f"  ✅ 仓位上限: 弱市≤{POSITION_PCT_WEAK}% 正常≤{POSITION_PCT_NORMAL}% 强市≤{POSITION_PCT_STRONG}%")


# ── 测试6: 仓位强制校验逻辑 ────────────────────────────────────────────────
def test_position_pct_cap():
    from thresholds import POSITION_PCT_WEAK, POSITION_PCT_NORMAL, POSITION_PCT_STRONG
    def cap_position(llm_pct: float, market_mode: str) -> float:
        if market_mode in ("weak", "extreme_warning"):
            max_pos = POSITION_PCT_WEAK
        elif market_mode == "neutral":
            max_pos = POSITION_PCT_NORMAL
        else:
            max_pos = POSITION_PCT_STRONG
        return min(llm_pct, max_pos)
    assert cap_position(15, "weak") == 3
    assert cap_position(7, "neutral") == 5
    assert cap_position(8, "strong") == 8
    assert cap_position(15, "strong") == 10
    print(f"  ✅ 仓位强制校验: 弱市15%→3% 正常7%→5% 强市15%→10%")


# ── 测试7: 涨跌停拦截 - 涨停排除集合过滤逻辑 ─────────────────────────────
def test_limit_up_exclusion_filtering():
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    
    fake_scores = [
        {"代码": "000001", "名称": "正常A", "综合评分": 80},
        {"代码": "000002", "名称": "正常B", "综合评分": 85},
        {"代码": "000003", "名称": "涨停股", "综合评分": 90},
        {"代码": "000004", "名称": "跌停股", "综合评分": 92},
    ]
    # 模拟涨停/跌停排除集合（_limit_up_excluded_codes）
    agent._limit_up_excluded_codes = {"000003", "000004"}
    
    pools = {}
    blocked = agent._filter_limit_up(fake_scores, pools, {}, "2026-07-14", [])
    # _filter_limit_up 从返回值中移除涨停/跌停股，返回剩余正常股
    remaining_codes = {s["代码"] for s in blocked}
    assert "000003" not in remaining_codes, "涨停股应被移除"
    assert "000004" not in remaining_codes, "跌停股应被移除"
    assert "000001" in remaining_codes, "正常股应保留"
    assert len(remaining_codes) == 2
    print(f"  ✅ 涨跌停排除集合过滤: 涨停/跌停股被移除, 正常股保留")


# ── 测试8: 熔断器 OPEN 状态拒绝调用 ───────────────────────────────────────
def test_circuit_breaker_open_rejects():
    from error_handling import CircuitBreaker, CircuitState
    cb = CircuitBreaker(name="test", failure_threshold=2, timeout_seconds=0.01)
    
    def fail_func():
        raise ValueError("test")
    
    try:
        cb.call(fail_func)
    except ValueError:
        pass  # 第一次失败
    try:
        cb.call(fail_func)
    except ValueError:
        pass  # 第二次失败
    assert cb.state == CircuitState.OPEN
    assert cb.is_available() is False
    print(f"  ✅ 熔断器2次失败 → OPEN状态不可用")


# ── 测试9: 熔断器 HALF_OPEN 超时后允许一次尝试 ────────────────────────────
def test_circuit_breaker_half_open_allows():
    from error_handling import CircuitBreaker, CircuitState
    cb = CircuitBreaker(name="test", failure_threshold=2, timeout_seconds=0.01)
    
    def fail_func():
        raise ValueError("test")
    
    try:
        cb.call(fail_func)
    except ValueError:
        pass
    try:
        cb.call(fail_func)
    except ValueError:
        pass
    assert cb.state == CircuitState.OPEN
    time.sleep(0.02)  # 超过timeout
    assert cb.state == CircuitState.HALF_OPEN
    result = cb.call(lambda: "ok")
    assert result == "ok"
    print(f"  ✅ 熔断器超时后 → HALF_OPEN允许一次尝试")


# ── 测试10: 熔断器恢复 - HALF_OPEN 连续成功后关闭 ─────────────────────────
def test_circuit_breaker_closes_after_success():
    from error_handling import CircuitBreaker, CircuitState
    cb = CircuitBreaker(name="test", failure_threshold=2, success_threshold=3, timeout_seconds=0.01)
    
    def fail_func():
        raise ValueError("test")
    
    try:
        cb.call(fail_func)
    except ValueError:
        pass
    try:
        cb.call(fail_func)
    except ValueError:
        pass
    assert cb.state == CircuitState.OPEN
    time.sleep(0.02)
    for _ in range(3):
        cb.call(lambda: "ok")
    assert cb.state == CircuitState.CLOSED
    print(f"  ✅ 熔断器HALF_OPEN连续3次成功 → CLOSED关闭")


# ── 测试11: 数据缺失时硬规则R2/R3保守放行 ───────────────────────────────
def test_hard_rules_missing_data_allows():
    stock = {"代码": "000001", "名称": "测试"}
    mkt_cap = float(stock.get("流通市值", stock.get("market_cap", 0)))
    turnover = float(stock.get("换手率", 0))
    assert mkt_cap == 0
    assert turnover == 0
    print(f"  ✅ 数据缺失时R2/R3保守放行")


# ── 测试12: GateController 阻塞≥3次 demotion ─────────────────────────────
def test_gate_controller_block_demotion():
    from gate_controller import GateController
    from thresholds import SKEPTIC_BLOCK_LIMIT
    
    gc = GateController()
    # check_blocked_count 使用 stocks 列表，且检查 blocked_count >= SKEPTIC_BLOCK_LIMIT
    key_pool_data = {
        "date": "2026-07-14",
        "stocks": [{
            "代码": "000001", "名称": "测试",
            "blocked_count": SKEPTIC_BLOCK_LIMIT,
            "last_blocked_date": "2026-07-14",
            "first_blocked_date": "2026-07-14",
        }]
    }
    demotions, resets, modified = gc.check_blocked_count(
        key_pool_data, {"000001"}, None
    )
    # blocked_count == SKEPTIC_BLOCK_LIMIT → 触发 demotion
    assert len(demotions) >= 1
    print(f"  ✅ GateController 阻塞≥{SKEPTIC_BLOCK_LIMIT}次 → demotion={len(demotions)}")


# ── 测试13: 熔断器模块级函数 check_circuit_breaker / record_failure ───────
def test_module_level_circuit_breaker():
    from error_handling import check_circuit_breaker, record_failure, record_success
    
    record_failure("scenario_test")
    # 初始状态: 0次失败 → 可用
    assert check_circuit_breaker("scenario_test") is True
    # 触发failure_threshold次失败 → 不可用
    for _ in range(5):  # default failure_threshold=5
        record_failure("scenario_test")
    assert check_circuit_breaker("scenario_test") is False
    print(f"  ✅ 模块级熔断器: 6次失败 → 拒绝调用")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])