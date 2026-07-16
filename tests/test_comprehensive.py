#!/usr/bin/env python3
"""多策略组合管理 — 全量综合测试（第四阶段）

覆盖5类44+项用例：
- FT: 功能测试
- BT: 边界测试
- AT: 异常测试
- CT: 兼容性测试
- PT: 性能测试
"""
import sys, os, time, math, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'agents'))
from pathlib import Path
from portfolio_manager import PortfolioManager, StrategyConfig, PortfolioState

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def clean():
    d = PROJECT_ROOT / "data" / "portfolio"
    if d.exists():
        import shutil
        shutil.rmtree(d)

def fresh_pm():
    """创建全新PortfolioManager（不受之前测试污染）"""
    clean()
    return PortfolioManager()

PASS, FAIL = 0, 0
results = []

def check(case_id, name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        results.append((case_id, name, "✅", ""))
    else:
        FAIL += 1
        results.append((case_id, name, "❌", detail))

def run_all():
    global PASS, FAIL
    t0 = time.time()

    # ═════════════════════════════════════════════════════
    # FT: 功能测试（24项）
    # ═════════════════════════════════════════════════════

    # FT-01~05: 策略池管理
    pm = fresh_pm()
    check("FT-01a", "策略注册成功", pm.register_strategy(StrategyConfig(name="FT_A", allocation=0.3)))
    check("FT-01b", "重复注册失败", not pm.register_strategy(StrategyConfig(name="FT_A")))
    pm.register_strategy(StrategyConfig(name="FT_B"))

    check("FT-02a", "停用成功", pm.disable_strategy("FT_B"))
    check("FT-02b", "停用后enabled=False", not pm._strategies["FT_B"].enabled)
    check("FT-02c", "启用成功", pm.enable_strategy("FT_B"))
    check("FT-02d", "启用后enabled=True", pm._strategies["FT_B"].enabled)

    check("FT-03a", "参数更新", pm.update_strategy("FT_A", max_drawdown=-20.0, max_position_pct=15.0))
    check("FT-03b", "参数生效", pm._strategies["FT_A"].max_drawdown == -20.0)
    check("FT-03c", "版本>=1.1", pm._strategies["FT_A"].version >= "1.1")
    check("FT-04", "版本历史>=2条", len(pm.get_version_history("FT_A")) >= 2)
    check("FT-05a", "回滚到v1.0", pm.rollback_version("FT_A", "1.0"))
    check("FT-05b", "回滚所有恢复", abs(pm._strategies["FT_A"].allocation - 0.3) < 0.01)

    # FT-06: 状态推导
    pm2 = fresh_pm()
    pm2.register_strategy(StrategyConfig(name="status_A", max_drawdown=-15.0))
    pm2.update_metrics("status_A", drawdown=-5.0)
    check("FT-06a", "正常状态=active", pm2.get_strategy_status("status_A")["status"] == "active")
    pm2.update_metrics("status_A", drawdown=-16.0)
    check("FT-06b", "熔断状态=circuit_triggered",
          pm2.get_strategy_status("status_A")["status"] == "circuit_triggered")

    # FT-07: 等比例分配
    pm3 = fresh_pm()
    pm3.register_strategy(StrategyConfig(name="E1")); pm3.register_strategy(StrategyConfig(name="E2"))
    pm3.register_strategy(StrategyConfig(name="E3")); pm3.register_strategy(StrategyConfig(name="E4"))
    r = pm3.allocate("equal")
    check("FT-07", "4策略等分25%", len(r) == 4 and all(abs(r[n] - 0.25) < 0.001 for n in ["E1","E2","E3","E4"]))

    # FT-08: 固定比例分配
    r = pm3.allocate_fixed({"E1": 7, "E2": 3})
    check("FT-08a", "固定E1=70%", abs(r["E1"] - 0.7) < 0.01)
    check("FT-08b", "固定E2=30%", abs(r["E2"] - 0.3) < 0.01)

    # FT-09: 风险平价
    r = pm3.allocate_risk_parity({"保守": 0.5, "激进": 2.0, "稳健": 1.0})
    check("FT-09a", "低波动多配", r["保守"] > r["稳健"] > r["激进"])
    check("FT-09b", "总和=1", abs(sum(r.values()) - 1.0) < 0.01)

    # FT-10: 凯利公式
    r = pm3.allocate_kelly({"A": 60, "B": 40}, {"A": 5, "B": 8}, {"A": 3, "B": 5})
    check("FT-10a", "凯利总和=1", abs(sum(r.values()) - 1.0) < 0.01)
    check("FT-10b", "凯利全非负", all(v >= 0 for v in r.values()))

    # FT-11~12: 绩效分配（独立clean确保无污染）
    pm4 = fresh_pm()
    pm4.register_strategy(StrategyConfig(name="PF_A")); pm4.register_strategy(StrategyConfig(name="PF_B"))
    r = pm4.allocate_by_performance({"PF_A": {"sharpe": 1.5}, "PF_B": {"sharpe": 0.5}}, "sharpe")
    check("FT-11", "高夏普多配", r["PF_A"] > r["PF_B"])

    # 清理旧策略重新注册
    pm4.remove_strategy("PF_A"); pm4.remove_strategy("PF_B")
    pm4.register_strategy(StrategyConfig(name="WR_A", max_allocation=0.6))
    pm4.register_strategy(StrategyConfig(name="WR_B", max_allocation=0.6))
    r = pm4.allocate_by_performance({"WR_A": {"win_rate": 30}, "WR_B": {"win_rate": 10}}, "win_rate")
    check("FT-12", "高胜率多配", r["WR_A"] > r["WR_B"] and r["WR_A"] > 0.05)

    # FT-13: 多层级分配
    pm5 = fresh_pm()
    pm5.register_strategy(StrategyConfig(name="ML_A", allocation=0.3, max_position_pct=10.0))
    pm5.register_strategy(StrategyConfig(name="ML_B", allocation=0.2, max_position_pct=10.0))
    pool = {"ML_A": [{"代码": "001", "评分": 90}, {"代码": "002", "评分": 80}],
            "ML_B": [{"代码": "003", "评分": 70}]}
    pos, alloc = pm5.allocate_multi_tier("equal", pool_data=pool, smooth_factor=1.0)
    check("FT-13a", "策略A有分配", "ML_A" in alloc)
    check("FT-13b", "标的001在A中", "001" in pos.get("ML_A", {}))
    check("FT-13c", "评分90>80", pos["ML_A"]["001"] > pos["ML_A"]["002"])

    # FT-14: 空策略
    pm6 = fresh_pm()
    check("FT-14", "空策略返回{}", pm6.allocate("equal") == {})

    # FT-15: 总持仓超限
    pm7 = fresh_pm()
    pm7.register_strategy(StrategyConfig(name="P_A", max_positions=3))
    alerts = pm7.check_portfolio_risk({"P_A": [{"code": str(i)} for i in range(31)]})
    check("FT-15", "总持仓超限告警", any("总持仓超限" in a for a in alerts))

    # FT-16+17: 熔断
    pm8 = fresh_pm()
    pm8.register_strategy(StrategyConfig(name="CB_A", max_drawdown=-10.0))
    pm8.update_metrics("CB_A", drawdown=-5.0)
    check("FT-16a", "未触发熔断", not pm8.check_strategy_circuit_breaker("CB_A"))
    pm8.update_metrics("CB_A", drawdown=-12.0)
    check("FT-16b", "触发熔断", pm8.check_strategy_circuit_breaker("CB_A"))
    # 熔断后分配应为0
    alloc_cb = pm8.allocate("equal")
    check("FT-16c", "熔断后分配=0", alloc_cb.get("CB_A", 1) == 0.0)
    pm8.update_metrics("CB_A", drawdown=-3.0)
    check("FT-17", "熔断恢复", not pm8.check_strategy_circuit_breaker("CB_A"))

    # FT-18+19: 相关性
    pm9 = fresh_pm()
    perf_abc = {"A": [0.01, 0.02, 0.0, 0.03], "B": [0.015, 0.025, 0.005, 0.035], "C": [-0.01, -0.02, 0.0, -0.03]}
    mat = pm9.update_correlation(perf_abc)
    alerts = pm9.check_correlation_alerts()
    check("FT-18", "相关矩阵有数据", len(mat) > 0)
    check("FT-19", "告警列表正常", isinstance(alerts, list))

    # FT-20: 跨策略暴露
    pm10 = fresh_pm()
    pm10.register_strategy(StrategyConfig(name="CS_A")); pm10.register_strategy(StrategyConfig(name="CS_B"))
    pos_cross = {"CS_A": [{"代码": "000001", "仓位": 20.0}], "CS_B": [{"代码": "000001", "仓位": 15.0}]}
    al = pm10.check_portfolio_risk(pos_cross)
    check("FT-20", "跨策略暴露告警", any("跨策略" in a for a in al))

    # FT-21+22: 偏离度再平衡
    pm11 = fresh_pm()
    pm11.register_strategy(StrategyConfig(name="RB_A", allocation=0.5, rebalance_threshold=0.1))
    pm11.register_strategy(StrategyConfig(name="RB_B", allocation=0.5, rebalance_threshold=0.1))
    n, _ = pm11.check_rebalance({"RB_A": 0.51, "RB_B": 0.49}, {"RB_A": 0.5, "RB_B": 0.5})
    check("FT-22", "小偏离不触发", not n)
    n, _ = pm11.check_rebalance({"RB_A": 0.7, "RB_B": 0.3}, {"RB_A": 0.5, "RB_B": 0.5})
    check("FT-21", "大偏离触发", n)

    # FT-23: 平滑调仓
    target_sm = {"RB_A": 0.6, "RB_B": 0.4}
    smooth = pm11._smooth_adjustment(target_sm, max_turnover=0.1)
    adj = sum(abs(smooth.get(k, 0) - 0.5) for k in ["RB_A", "RB_B"])
    check("FT-23", "平滑限制调仓幅度", adj <= 0.15)

    # FT-24: 优胜劣汰
    pm12 = fresh_pm()
    pm12.register_strategy(StrategyConfig(name="S_A", allocation=0.3))
    pm12.register_strategy(StrategyConfig(name="S_B", allocation=0.3))
    pm12.register_strategy(StrategyConfig(name="S_C", allocation=0.3))
    pm12.update_metrics("S_A", sharpe=2.0, win_rate=70, drawdown=-5.0)
    pm12.update_metrics("S_B", sharpe=1.5, win_rate=60, drawdown=-8.0)
    pm12.update_metrics("S_C", sharpe=0.5, win_rate=40, drawdown=-20.0)
    el = pm12.survival_competition()
    check("FT-24a", "C被淘汰", "S_C" in el)
    check("FT-24b", "A保留", "S_A" not in el)

    # ═════════════════════════════════════════════════════
    # BT: 边界测试（8项）
    # ═════════════════════════════════════════════════════
    pm_b = fresh_pm()

    check("BT-01", "空权重返回{}", pm_b.allocate("fixed", ratios={}) == {})
    r = pm_b.allocate_kelly({"A": 0}, {"A": 5}, {"A": 3})
    check("BT-02", "凯利win_rate=0", r.get("A", 1) == 0)
    r = pm_b.allocate_kelly({"A": 60}, {"A": 0}, {"A": 3})
    check("BT-03", "凯利avg_wins=0不崩", all(v >= 0 for v in r.values()))
    r = pm_b.allocate_risk_parity({"A": 0})
    check("BT-04", "波动率=0不崩", len(r) > 0)
    r = pm_b.allocate_fixed({"A": 10, "B": 5})
    check("BT-05", "总和归一化100%", abs(sum(r.values()) - 1.0) < 0.01)
    check("BT-06", "无策略返回{}", pm_b.allocate("equal") == {})

    pm_b.register_strategy(StrategyConfig(name="BT_CB", max_drawdown=-5.0))
    pm_b.update_metrics("BT_CB", drawdown=-4.9)
    check("BT-07", "回撤-4.9不熔断", not pm_b.check_strategy_circuit_breaker("BT_CB"))
    pm_b.update_metrics("BT_CB", drawdown=-5.0)
    check("BT-08", "回撤-5.0触发熔断", pm_b.check_strategy_circuit_breaker("BT_CB"))

    # ═════════════════════════════════════════════════════
    # AT: 异常测试（6项）
    # ═════════════════════════════════════════════════════
    pm_a = fresh_pm()
    pm_a.register_strategy(StrategyConfig(name="AT_A"))

    check("AT-01", "更新不存在策略->False", not pm_a.update_strategy("not_exist", allocation=0.5))
    check("AT-02a", "启用不存在->False", not pm_a.enable_strategy("not_exist"))
    check("AT-02b", "停用不存在->False", not pm_a.disable_strategy("not_exist"))
    check("AT-03", "空波动率->{}", pm_a.allocate_risk_parity({}) == {})

    p_miss = {"AT_A": [{"代码": "001"}, {"代码": "002", "评分": 80}]}
    pos, _ = pm_a.allocate_multi_tier("equal", pool_data=p_miss, smooth_factor=1.0)
    check("AT-04", "缺失评分不崩溃", "AT_A" in pos)
    check("AT-05", "移除不存在->False", not pm_a.remove_strategy("not_exist"))
    check("AT-06", "回滚不存在->False", not pm_a.rollback_version("AT_A", "9.9"))

    # ═════════════════════════════════════════════════════
    # CT: 兼容性测试（4项）
    # ═════════════════════════════════════════════════════
    clean()
    try:
        from pool_manager import PoolManager
        PoolManager()
        check("CT-01", "PoolManager导入正常", True)
    except Exception as e:
        check("CT-01", "PoolManager导入正常", False, str(e))

    try:
        from gate_controller import GateController
        check("CT-01b", "GateController导入正常", True)
    except Exception as e:
        check("CT-01b", "GateController导入正常", False, str(e))

    import yaml
    cfg_path = PROJECT_ROOT / "config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text())
        check("CT-02", "config.yaml含portfolio节", "portfolio" in cfg)

    try:
        pm_ct = fresh_pm()
        check("CT-04", "初始化不崩溃", True)
    except Exception as e:
        check("CT-04", "初始化不崩溃", False, str(e))

    # ═════════════════════════════════════════════════════
    # PT: 性能测试（2项）
    # ═════════════════════════════════════════════════════
    pm_p = fresh_pm()

    t1 = time.time()
    perf_10 = {f"S{i}": [math.sin(i * 0.1 * t) for t in range(30)] for i in range(10)}
    mat10 = pm_p.update_correlation(perf_10)
    elapsed = time.time() - t1
    check("PT-01", f"10策略相关性{elapsed*1000:.0f}ms<=500ms", elapsed <= 0.5)

    t1 = time.time()
    perf_1k = [i * 0.001 for i in range(1000)]
    metrics = pm_p.calc_risk_metrics(perf_1k)
    elapsed = time.time() - t1
    check("PT-02", f"1000条绩效{elapsed*1000:.0f}ms<=200ms", elapsed <= 0.2)

    # ═════════════════════════════════════════════════════
    # 汇总
    # ═════════════════════════════════════════════════════
    elapsed_total = time.time() - t0
    print(f"\n{'='*50}")
    print(f"  全量综合测试完成: {PASS}/{PASS+FAIL} 通过")
    print(f"  总耗时: {elapsed_total:.2f}s")
    if FAIL:
        print(f"  ❌ {FAIL} 个失败")
        for cid, name, st, detail in results:
            if st != "✅":
                print(f"    {cid} {name}: {detail}")
    else:
        print(f"  ✅ 全部通过")

    cats = {}
    for cid, _, st, _ in results:
        prefix = cid.split("-")[0]
        if prefix not in cats:
            cats[prefix] = {"pass": 0, "total": 0}
        cats[prefix]["total"] += 1
        if st == "✅":
            cats[prefix]["pass"] += 1
    print(f"\n  分类通过率:")
    for k, v in sorted(cats.items()):
        print(f"    {k}: {v['pass']}/{v['total']}")

    clean()


if __name__ == "__main__":
    run_all()