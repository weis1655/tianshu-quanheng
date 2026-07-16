#!/usr/bin/env python3
"""多策略组合管理模块 — 全量测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'agents'))
import json, tempfile
from pathlib import Path
from portfolio_manager import PortfolioManager, StrategyConfig, PortfolioState

# ── 测试前清理 ────────────────────────────────────
def clean_test_data():
    """清理测试数据"""
    project_root = Path(__file__).resolve().parent.parent
    portfolio_dir = project_root / "data" / "portfolio"
    if portfolio_dir.exists():
        import shutil
        shutil.rmtree(portfolio_dir)

clean_test_data()

# ── 辅助函数 ────────────────────────────────────

def test_setup():
    """测试基础初始化"""
    clean_test_data()
    pm = PortfolioManager()
    assert pm is not None
    assert pm.list_strategies() == []
    print("✅ 基础初始化通过")

def test_register_strategy():
    """测试策略注册"""
    clean_test_data()
    pm = PortfolioManager()
    cfg = StrategyConfig(name="test_strat_A", allocation=0.3)
    assert pm.register_strategy(cfg)
    assert len(pm.list_strategies()) == 1
    assert pm.list_strategies()[0].name == "test_strat_A"
    # 重复注册应失败
    assert not pm.register_strategy(cfg)
    print("✅ 策略注册通过")

def test_enable_disable():
    """测试策略启停"""
    clean_test_data()
    pm = PortfolioManager()
    cfg = StrategyConfig(name="test_B", allocation=0.2)
    pm.register_strategy(cfg)
    assert pm.list_strategies()[0].enabled
    pm.disable_strategy("test_B")
    assert not pm._strategies["test_B"].enabled
    pm.enable_strategy("test_B")
    assert pm._strategies["test_B"].enabled
    # 获取已启用策略
    assert len(pm.get_enabled_strategies()) == 1
    print("✅ 策略启停通过")

def test_update_strategy():
    """测试策略参数更新"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="test_C", allocation=0.3))
    pm.update_strategy("test_C", max_drawdown=-20.0, max_position_pct=15.0)
    assert pm._strategies["test_C"].max_drawdown == -20.0
    assert pm._strategies["test_C"].max_position_pct == 15.0
    print("✅ 策略参数更新通过")

def test_allocate_equal():
    """测试等比例分配"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", allocation=0))
    pm.register_strategy(StrategyConfig(name="B", allocation=0))
    pm.register_strategy(StrategyConfig(name="C", allocation=0))
    result = pm.allocate("equal")
    assert len(result) == 3
    assert abs(result["A"] - 1/3) < 0.001
    assert abs(result["B"] - 1/3) < 0.001
    assert abs(result["C"] - 1/3) < 0.001
    print("✅ 等比例分配通过")

def test_allocate_fixed():
    """测试固定比例分配"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", allocation=0))
    pm.register_strategy(StrategyConfig(name="B", allocation=0))
    result = pm.allocate_fixed({"A": 7.0, "B": 3.0})
    assert abs(result["A"] - 0.7) < 0.01
    assert abs(result["B"] - 0.3) < 0.01
    print("✅ 固定比例分配通过")

def test_allocate_risk_parity():
    """测试风险平价分配"""
    clean_test_data()
    pm = PortfolioManager()
    vols = {"保守": 0.5, "激进": 2.0, "稳健": 1.0}
    result = pm.allocate_risk_parity(vols)
    # 波动率越低分配越多
    assert result["保守"] > result["稳健"] > result["激进"]
    assert abs(sum(result.values()) - 1.0) < 0.01
    print("✅ 风险平价分配通过")

def test_allocate_kelly():
    """测试凯利公式分配"""
    clean_test_data()
    pm = PortfolioManager()
    result = pm.allocate_kelly(
        win_rates={"A": 60, "B": 40},
        avg_wins={"A": 5.0, "B": 8.0},
        avg_losses={"A": 3.0, "B": 5.0})
    assert abs(sum(result.values()) - 1.0) < 0.01
    assert all(v >= 0 for v in result.values())
    print("✅ 凯利公式分配通过")

def test_allocate_by_performance():
    """测试绩效分配"""
    clean_test_data()
    pm = PortfolioManager()
    metrics = {
        "A": {"sharpe": 1.5, "win_rate": 60, "total_return": 5.0},
        "B": {"sharpe": 0.5, "win_rate": 40, "total_return": -2.0},
    }
    result = pm.allocate_by_performance(metrics, "sharpe")
    assert result["A"] > result["B"]
    print("✅ 绩效分配通过")

def test_portfolio_risk():
    """测试组合风控"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", max_positions=3))
    pm.register_strategy(StrategyConfig(name="B", max_drawdown=-10))
    # 正常
    alerts = pm.check_portfolio_risk({"A": [{"code":"001"},{"code":"002"}], "B": []})
    assert len(alerts) == 0
    # 超限
    alerts = pm.check_portfolio_risk({"A": [{"code":"001"},{"code":"002"},{"code":"003"},{"code":"004"}], "B": []})
    assert len(alerts) > 0
    print("✅ 组合风控通过")

def test_rebalance():
    """测试再平衡"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", allocation=0.5, rebalance_threshold=0.1))
    pm.register_strategy(StrategyConfig(name="B", allocation=0.5, rebalance_threshold=0.1))
    # 偏离度小于阈值 → 不触发
    needs, _ = pm.check_rebalance({"A": 0.51, "B": 0.49}, {"A": 0.5, "B": 0.5})
    assert not needs
    # 偏离度大于阈值 → 触发
    needs, _ = pm.check_rebalance({"A": 0.7, "B": 0.3}, {"A": 0.5, "B": 0.5})
    assert needs
    print("✅ 再平衡检测通过")

def test_metrics_update():
    """测试策略指标更新"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A"))
    pm.update_metrics("A", drawdown=-5.0, sharpe=1.2, win_rate=55)
    assert pm._strategies["A"].metrics["drawdown"] == -5.0
    assert pm._strategies["A"].metrics["sharpe"] == 1.2
    print("✅ 策略指标更新通过")

def test_remove_strategy():
    """测试移除策略"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A"))
    assert len(pm.list_strategies()) == 1
    pm.remove_strategy("A")
    assert len(pm.list_strategies()) == 0
    print("✅ 移除策略通过")

def test_persist():
    """测试持久化"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="persist_test", allocation=0.5))
    # 重新加载
    pm2 = PortfolioManager()
    found = [s for s in pm2.list_strategies() if s.name == "persist_test"]
    assert len(found) == 1
    assert abs(found[0].allocation - 0.5) < 0.01
# 清理
    pm.remove_strategy("persist_test")
    print("✅ 持久化通过")


# ═══════════════════════════════════════════════════
# 新增功能测试（PM-001 ~ PM-009）
# ═══════════════════════════════════════════════════

def test_version_management():
    """PM-001: 版本管理"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="vtest", allocation=0.3))
    assert pm._strategies["vtest"].version == "1.0"
    pm.update_strategy("vtest", allocation=0.5, max_drawdown=-20.0)
    assert pm._strategies["vtest"].version == "1.1"
    pm.update_strategy("vtest", allocation=0.4)
    assert pm._strategies["vtest"].version == "1.2"
    # 版本历史
    history = pm.get_version_history("vtest")
    assert len(history) == 3  # 初始+2次更新
    # 回滚
    ok = pm.rollback_version("vtest", "1.0")
    assert ok
    assert abs(pm._strategies["vtest"].allocation - 0.3) < 0.01
    print("✅ 版本管理通过")

def test_strategy_status():
    """PM-001: 策略状态推导"""
    clean_test_data()
    pm = PortfolioManager()
    s = StrategyConfig(name="status_test", max_drawdown=-15.0)
    pm.register_strategy(s)
    # active
    pm.update_metrics("status_test", drawdown=-5.0)
    st = pm.get_strategy_status("status_test")
    assert st["status"] == "active", f"Expected active, got {st['status']}"
    # circuit_triggered
    pm.update_metrics("status_test", drawdown=-16.0)
    st = pm.get_strategy_status("status_test")
    assert st["status"] == "circuit_triggered", f"Expected circuit_triggered, got {st['status']}"
    print("✅ 策略状态推导通过")

def test_multi_tier_allocation():
    """PM-002: 多层级资金分配"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", allocation=0.3, max_position_pct=10.0))
    pm.register_strategy(StrategyConfig(name="B", allocation=0.2, max_position_pct=10.0))
    pool_data = {
        "A": [{"代码": "000001", "评分": 90}, {"代码": "000002", "评分": 80}],
        "B": [{"代码": "000003", "评分": 70}],
    }
    positions, allocs = pm.allocate_multi_tier("equal", pool_data=pool_data, smooth_factor=1.0)
    assert "A" in allocs
    assert "B" in allocs
    assert "000001" in positions.get("A", {})
    assert "000003" in positions.get("B", {})
    # 评分90比80多配
    assert positions["A"]["000001"] > positions["A"]["000002"]
    print("✅ 多层级分配通过")

def test_correlation():
    """PM-003: 相关性监控"""
    clean_test_data()
    pm = PortfolioManager()
    perf_data = {
        "A": [0.01, 0.02, -0.01, 0.03, 0.01, 0.0, -0.02, 0.02],
        "B": [0.015, 0.025, -0.005, 0.035, 0.015, 0.005, -0.015, 0.025],
        "C": [-0.01, -0.02, 0.01, -0.03, -0.01, 0.0, 0.02, -0.02],  # 反相关
    }
    mat = pm.update_correlation(perf_data)
    assert "A" in mat
    assert "B" in mat
    # A-B 正相关
    assert mat["A"]["B"] > 0.5, f"A-B should be positively correlated: {mat['A']['B']}"
    # A-C 应接近负相关
    ref = mat["A"]["C"]
    assert ref < 0, f"A-C should be negatively correlated: {ref}"
    # 告警检查
    alerts = pm.check_correlation_alerts()
    assert isinstance(alerts, list)
    print("✅ 相关性监控通过")

def test_circuit_breaker():
    """PM-004: 策略熔断"""
    clean_test_data()
    pm = PortfolioManager()
    s = StrategyConfig(name="cb_test", max_drawdown=-10.0)
    pm.register_strategy(s)
    # 未触发
    pm.update_metrics("cb_test", drawdown=-5.0)
    triggered = pm.check_strategy_circuit_breaker("cb_test")
    assert not triggered
    assert not pm._circuit_breakers.get("cb_test", False)
    # 回撤超限触发熔断
    pm.update_metrics("cb_test", drawdown=-12.0)
    triggered = pm.check_strategy_circuit_breaker("cb_test")
    assert triggered
    assert pm._circuit_breakers.get("cb_test", True)
    # 熔断后分配为0
    alloc = pm.allocate("equal")
    assert alloc.get("cb_test", 1.0) == 0.0, f"熔断策略应分配0: {alloc}"
    # 恢复
    pm.update_metrics("cb_test", drawdown=-3.0)
    triggered = pm.check_strategy_circuit_breaker("cb_test")
    assert not triggered
    assert not pm._circuit_breakers.get("cb_test", False)
    print("✅ 策略熔断通过")

def test_cross_strategy_exposure():
    """PM-005: 跨策略暴露检查"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", max_positions=5))
    pm.register_strategy(StrategyConfig(name="B", max_positions=5))
    # 同标的多策略持仓
    positions = {
        "A": [{"代码": "000001", "仓位": 20.0, "股票名称": "平安银行"}],
        "B": [{"代码": "000001", "仓位": 15.0, "股票名称": "平安银行"}, {"代码": "000002", "仓位": 5.0}],
    }
    alerts = pm.check_portfolio_risk(positions)
    cross_alerts = [a for a in alerts if "跨策略" in a]
    assert len(cross_alerts) > 0, f"应有跨策略告警: {alerts}"
    print("✅ 跨策略暴露检查通过")

def test_smooth_rebalance():
    """PM-006: 平滑调仓"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", allocation=0.5))
    pm.register_strategy(StrategyConfig(name="B", allocation=0.5))
    target = {"A": 0.6, "B": 0.4}
    # 总调整幅度0.2 > max_turnover=0.2 → 压缩
    smooth = pm._smooth_adjustment(target, max_turnover=0.1)
    total_adj = sum(abs(smooth.get(k, 0) - 0.5) for k in ["A", "B"])
    assert total_adj <= 0.11  # 略大于0.1（归一化误差）
    # 执行再平衡
    result = pm.rebalance(method="equal", smooth=True)
    assert abs(sum(result.values()) - 1.0) < 0.01
    print("✅ 平滑调仓通过")

def test_survival_competition():
    """PM-007: 优胜劣汰"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", allocation=0.3))
    pm.register_strategy(StrategyConfig(name="B", allocation=0.3))
    pm.register_strategy(StrategyConfig(name="C", allocation=0.3))
    # 为C设最差指标 → 应被淘汰
    pm.update_metrics("A", sharpe=2.0, win_rate=70, drawdown=-5.0)
    pm.update_metrics("B", sharpe=1.5, win_rate=60, drawdown=-8.0)
    pm.update_metrics("C", sharpe=0.5, win_rate=40, drawdown=-20.0)
    eliminated = pm.survival_competition()
    assert "C" in eliminated, f"C should be eliminated: {eliminated}"
    assert "A" not in eliminated
    assert len(pm.list_strategies()) == 2
    print("✅ 优胜劣汰通过")

def test_risk_metrics():
    """PM-008: 风险指标计算"""
    clean_test_data()
    pm = PortfolioManager()
    # 持续上涨的日收益率
    returns = [0.001] * 60
    metrics = pm.calc_risk_metrics(returns)
    assert metrics["sharpe"] > 0, f"Sharpe should be positive: {metrics['sharpe']}"
    assert metrics["sortino"] > 0
    assert metrics["volatility"] > 0
    # 夏普和索提诺相近（无下行风险）
    assert abs(metrics["sharpe"] - metrics["sortino"]) < 1.0
    # VaR应接近0（全部正收益）
    assert metrics["var_95"] >= 0
    print("✅ 风险指标计算通过")

def test_attribution():
    """PM-009: 归因分析"""
    clean_test_data()
    pm = PortfolioManager()
    pm.register_strategy(StrategyConfig(name="A", allocation=0.6))
    pm.register_strategy(StrategyConfig(name="B", allocation=0.4))
    # 更新策略实际分配
    pm.update_strategy("A", allocation=0.6)
    pm.update_strategy("B", allocation=0.4)
    strat_returns = {"A": 0.02, "B": -0.01}
    attr = pm.calc_attribution(strat_returns)
    assert "_portfolio_total" in attr
    assert "A" in attr
    assert "B" in attr
    # A贡献应 > B贡献
    assert attr["A"] > attr["B"], f"A contribution {attr['A']} should > B {attr['B']}"
    # 单策略贡献度
    contrib = pm.calc_strategy_contribution("A", 0.02)
    assert contrib["strategy"] == "A"
    assert contrib["allocation_pct"] == 60.0
    print("✅ 归因分析通过")

def test_capacity_limits():
    """PM-005: 容量上限检查"""
    clean_test_data()
    pm = PortfolioManager()
    s = StrategyConfig(name="cap_test", max_positions=3, max_allocation=0.5)
    pm.register_strategy(s)
    pm.update_strategy("cap_test", allocation=0.6)
    alerts = pm.check_capacity_limits("cap_test", [{"代码":"001"}, {"代码":"002"}, {"代码":"003"}, {"代码":"004"}])
    assert len(alerts) >= 1, f"应有容量告警: {alerts}"
    print("✅ 容量上限检查通过")


    # ── 入口 ────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        test_setup, test_register_strategy, test_enable_disable,
        test_update_strategy, test_allocate_equal, test_allocate_fixed,
        test_allocate_risk_parity, test_allocate_kelly,
        test_allocate_by_performance, test_portfolio_risk,
        test_rebalance, test_metrics_update, test_remove_strategy,
        test_persist,
        # 新增功能测试
        test_version_management, test_strategy_status,
        test_multi_tier_allocation, test_correlation,
        test_circuit_breaker, test_cross_strategy_exposure,
        test_smooth_rebalance, test_survival_competition,
        test_risk_metrics, test_attribution, test_capacity_limits,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1

    # 清理测试产生的文件
    import shutil
    project_root = Path(__file__).resolve().parent.parent
    portfolio_dir = project_root / "data" / "portfolio"
    if portfolio_dir.exists():
        shutil.rmtree(portfolio_dir)
    
    # 也清理旧的测试目录（兼容性清理）
    test_dir = project_root / "data" / "strategies"
    if test_dir.exists():
        for f in test_dir.glob("*.json"):
            f.unlink()
    state_file = project_root / "data" / "portfolio_state.json"
    if state_file.exists():
        state_file.unlink()
    history_dir = project_root / "data" / "portfolio_history"
    if history_dir.exists():
        for f in history_dir.glob("*.json"):
            f.unlink()

    print(f"\n{'='*40}")
    print(f"  测试结果: {passed}/{passed+failed} 通过")
    if failed:
        print(f"  ❌ {failed} 个失败")
    else:
        print(f"  ✅ 全部通过")