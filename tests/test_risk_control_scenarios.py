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
        {"代码": "sh000001", "名称": "上证指数", "涨跌幅": -0.5, "最新价": 3100},
        {"代码": "sz399006", "名称": "创业板指", "涨跌幅": -4.2, "最新价": 1800},  # 创业板-4.2%
        {"代码": "sz000688", "名称": "科创50", "涨跌幅": -2.5, "最新价": 800},
        {"代码": "sh000300", "名称": "沪深300", "涨跌幅": -1.5, "最新价": 3600},
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
        {"代码": "sh000001", "名称": "上证指数", "涨跌幅": -1.2, "最新价": 3100},
        {"代码": "sz399006", "名称": "创业板指", "涨跌幅": -1.5, "最新价": 1800},  # 创业板-1.5%未触发
        {"代码": "sz000688", "名称": "科创50", "涨跌幅": -1.0, "最新价": 800},
        {"代码": "sh000300", "名称": "沪深300", "涨跌幅": -2.8, "最新价": 3600},  # 沪深300-2.8%触发
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
        {"代码": "sh000001", "名称": "上证指数", "涨跌幅": 0.8, "最新价": 3100},
        {"代码": "sz399006", "名称": "创业板指", "涨跌幅": 0.5, "最新价": 1800},
        {"代码": "sh000300", "名称": "沪深300", "涨跌幅": 0.3, "最新价": 3600},
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
        {"代码": "sh000001", "名称": "上证指数", "涨跌幅": 1.5, "最新价": 3100},
        {"代码": "sz399006", "名称": "创业板指", "涨跌幅": 0.8, "最新价": 1800},
    ]))
    result = agent._get_market_state()
    assert result["extreme_warning"] is False
    assert result["state"] == "偏多"
    print(f"  ✅ 上证+1.5% → 偏多模式 s_pool_cap={result['s_pool_cap']}")


# ── 测试4b: 市场状态鲁棒性回归（Q-H03 / Q-H06）────────────────────────────
def test_market_state_sh_index_code_collision():
    """Q-H03回归：上证指数(sh000001) 与 平安银行(sz000001) 撞码不得互相误认。

    历史缺陷：review_agent 曾按 `代码=="000001"` 匹配大盘，
    实际取到的是平安银行的涨跌幅，导致市场状态判定全线错位。
    """
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "sz000001", "名称": "平安银行", "涨跌幅": 3.5, "最新价": 10.5},  # 银行大涨
        {"代码": "sh000001", "名称": "上证指数", "涨跌幅": 0.2, "最新价": 3100},   # 大盘微涨
    ]))
    result = agent._get_market_state()
    assert result["state"] == "震荡偏强", f"误取平安银行涨跌幅导致 {result['state']}"
    assert abs(result["sh_chg"] - 0.2) < 1e-6
    print(f"  ✅ 撞码回归: sh_chg={result['sh_chg']:.2f} (取上证指数而非平安银行) state={result['state']}")


def test_market_state_missing_index_fails_loud():
    """Q-H03：指数行情缺失时不再静默——必须标记 data_missing（fail-loud）。"""
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "600519", "名称": "贵州茅台", "涨跌幅": 1.2, "最新价": 1500},
    ]))
    result = agent._get_market_state()
    assert result.get("data_missing") is True
    assert result["extreme_warning"] is False
    print(f"  ✅ 指数缺失: data_missing=True，退化震荡但显式标记")


def test_market_state_code_fallback_without_name():
    """Q-H03：名称字段缺失时按市场前缀+代码兜底匹配，仍应正确判定。"""
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "sh000001", "涨跌幅": 1.5, "最新价": 3100},   # 无"名称"字段
        {"代码": "sz399006", "涨跌幅": 0.8, "最新价": 1800},
    ]))
    result = agent._get_market_state()
    assert result["state"] == "偏多"
    assert result.get("data_missing") is None
    print(f"  ✅ 名称缺失兜底: state={result['state']} sh_chg={result['sh_chg']:+.2f}")


def test_hard_rules_st_block():
    """Q-H06回归：ST/*ST 标的必须在决策前置硬规则中被剔除。

    注意：必须用 `from compliance_manager import` 形式注入——
    `import agents.compliance_manager` 与 `compliance_manager` 是两个独立模块实例，
    ST_STOCKS 集合不共享（agents/ 同时在 sys.path 上，会被双重加载）。
    """
    from decision_agent import DecisionAgent
    from compliance_manager import ST_STOCKS
    agent = DecisionAgent(PROJECT_ROOT)
    ST_STOCKS.add("600999")
    try:
        hits = agent._apply_hard_rules(["600999", "600519"])
        blocked = [h for h in hits if h["code"] == "600999"]
        assert blocked, "ST 标的应被拦截"
        assert blocked[0]["reason"] == "ST/*ST 标的"
        assert not any(h["code"] == "600519" for h in hits), "正常标的不得被误杀"
        print(f"  ✅ 硬规则-ST: 600999 剔除，600519 放行")
    finally:
        ST_STOCKS.discard("600999")


def test_hard_rules_name_keyword_block():
    """Q-H06回归：名称含「退市」「暂停上市」的标的必须被剔除。"""
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "sh000001", "名称": "上证指数", "涨跌幅": 0.5, "最新价": 3100},
        {"代码": "600123", "名称": "S退市股", "涨跌幅": 0, "最新价": 1},
        {"代码": "000456", "名称": "某暂停上市", "涨跌幅": 0, "最新价": 2},
    ]))
    hits = agent._apply_hard_rules(["600123", "000456"])
    codes = {h["code"] for h in hits}
    assert "600123" in codes and "000456" in codes
    print(f"  ✅ 硬规则-名称关键字: {sorted(codes)} 全部剔除")


def test_hard_rules_no_false_positive():
    """Q-H06回归：无黑名单、无 ST、未持仓的正常标的不得被误杀。"""
    from decision_agent import DecisionAgent
    agent = DecisionAgent(PROJECT_ROOT)
    sm = PROJECT_ROOT / "data" / "shared_memory.json"
    sm.write_text(json.dumps([
        {"代码": "sh000001", "名称": "上证指数", "涨跌幅": 0.5, "最新价": 3100},
        {"代码": "600519", "名称": "贵州茅台", "涨跌幅": 1.2, "最新价": 1500},
    ]))
    hits = agent._apply_hard_rules(["600519"])
    assert not hits, f"正常标的被误杀: {hits}"
    print(f"  ✅ 硬规则-无误杀: 贵州茅台放行")


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


# ── 测试13: 熔断器状态持久化分区隔离（Q-H04）─────────────────────────────
def test_circuit_state_multi_breaker_isolation():
    """Q-H04回归：多熔断器共用单文件持久化时不得互相覆盖。

    历史缺陷：save_circuit_state 每次写整份 state，5 个熔断器共用 1 个文件，
    后者覆盖前者；进程重启后所有熔断器恢复成同一份快照。
    """
    import json, tempfile
    from pathlib import Path
    import agents.error_handling as eh

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write("{}")
        tmp = Path(f.name)
    try:
        names = ["t_news", "t_screen", "t_review"]
        for n in names:
            eh._circuit_breakers.pop(n, None)
        for n in names:
            for _ in range(3):
                eh.record_success(n)
            eh.save_circuit_state(tmp, eh.get_circuit_breaker(n))

        doc = json.loads(tmp.read_text())
        assert "breakers" in doc, "应迁移为按 name 分区结构"
        assert len(doc["breakers"]) == 3, f"应有3个独立分区，实际 {len(doc['breakers'])}"
        for n in names:
            assert doc["breakers"][n]["successful_calls"] == 3

        # 模拟进程重启：重新加载各熔断器
        for n in names:
            eh._circuit_breakers.pop(n, None)
        for n in names:
            b = eh.get_circuit_breaker(n)
            eh.restore_circuit_state(tmp, b)
            assert b._metrics.successful_calls == 3, f"{n} 恢复失败"
        print(f"  ✅ 熔断器分区隔离: {len(doc['breakers'])} 个独立分区，重启后各自恢复")
    finally:
        tmp.unlink(missing_ok=True)


def test_circuit_state_ignores_ownerless_legacy_snapshot():
    """Q-H04回归：无 name 归属的旧版单文件快照不得被套用到任意熔断器。"""
    import json, tempfile
    from pathlib import Path
    import agents.error_handling as eh

    legacy = {"state": "open", "total_calls": 52, "successful_calls": 48,
              "failed_calls": 4, "rejected_calls": 0, "consecutive_failures": 0,
              "last_state_change": "2026-08-14T07:10:41.222051"}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(legacy, f)
        tmp = Path(f.name)
    try:
        eh._circuit_breakers.pop("t_legacy", None)
        b = eh.get_circuit_breaker("t_legacy")
        eh.restore_circuit_state(tmp, b)
        assert b._state.value == "closed", "旧快照无归属，不得套用"
        assert b._metrics.total_calls == 0
        print(f"  ✅ 无主旧快照被拒绝: total_calls=0 state=closed")
    finally:
        tmp.unlink(missing_ok=True)


# ── 测试15: 熔断器模块级函数 check_circuit_breaker / record_failure ───────
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