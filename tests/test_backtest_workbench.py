#!/usr/bin/env python3
"""批量回测工作台 — 全量测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'agents'))
from backtest_workbench import (
    StrategyParam, BacktestResult, BatchTask, TaskStatus,
    BacktestSimulator, ParameterScanner, CompareAnalyzer,
    ReportGenerator, TaskManager, BatchWorkbench, ParamScanResult
)

PASS, FAIL = 0, 0

def check(cid, name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  ❌ {cid} {name}: {detail}")

def clean():
    import shutil
    d = os.path.join(os.path.dirname(__file__), '..', 'data', 'backtest_workbench')
    if os.path.exists(d):
        shutil.rmtree(d)

def test_data_model():
    p = StrategyParam(name="测试", min_score=75, stop_loss_pct=-5, hold_days=5)
    check("DM-01", "策略参数", p.name == "测试")
    check("DM-02", "默认评分", p.min_score == 75)

    r = BacktestResult(strategy_name="测试", params=p, total_trades=100)
    r.sharpe = 1.5
    check("DM-03", "回测结果", r.total_trades == 100)
    check("DM-04", "夏普", r.sharpe == 1.5)

    t = BatchTask(task_id="T001", name="测试任务")
    check("DM-05", "任务创建", t.task_id == "T001")
    check("DM-06", "默认状态", t.status == TaskStatus.PENDING)

def test_simulator():
    p = StrategyParam(name="模拟测试", min_score=75)
    r = BacktestSimulator.run(p)
    check("SM-01", "回测可运行", r.total_trades > 0)
    check("SM-02", "有胜率", 10 <= r.win_rate <= 90)
    check("SM-03", "有夏普", isinstance(r.sharpe, float))
    check("SM-04", "有回撤", r.max_drawdown <= 0)
    check("SM-05", "有盈亏比", r.profit_factor > 0)
    check("SM-06", "有期望值", isinstance(r.expectancy, float))

    # 批量
    results = BacktestSimulator.run_batch([p, p, p])
    check("SM-07", "批量回测", len(results) == 3)

def test_param_scan():
    base = StrategyParam(name="基准", min_score=75)
    scan = ParameterScanner.scan(base, "min_score")
    check("PS-01", "参数扫描有结果", len(scan.results) > 0)
    check("PS-02", "有最优参数", scan.best_param is not None)
    check("PS-03", "参数值递增", all(scan.param_values[i] <= scan.param_values[i+1] for i in range(len(scan.param_values)-1)))
    check("PS-04", "有敏感性", len(scan.sensitivity) > 0)

    # 多参数扫描
    multi = ParameterScanner.multi_scan(base, ["min_score", "hold_days"])
    check("PS-05", "多参数扫描", len(multi) == 2)
    check("PS-06", "生成值列表", len(ParameterScanner.generate_values("min_score")) > 0)

def test_compare():
    base = StrategyParam(name="基准", min_score=75)
    results = [
        BacktestResult(strategy_name="A", params=base, total_return_pct=20, win_rate=55, sharpe=1.2, sortino=1.0, profit_factor=2.0, max_drawdown=-10, max_consecutive_losses=3, payoff_ratio=2.0, calmar=1.0),
        BacktestResult(strategy_name="B", params=base, total_return_pct=10, win_rate=45, sharpe=0.8, sortino=0.7, profit_factor=1.5, max_drawdown=-20, max_consecutive_losses=5, payoff_ratio=1.5, calmar=0.5),
    ]
    ranked = CompareAnalyzer.rank(results)
    check("CP-01", "排名结果", len(ranked) == 2)
    check("CP-02", "A排第一", ranked[0][1].strategy_name == "A")

    diag = CompareAnalyzer.diagnosis(results[0])
    check("CP-03", "诊断有结果", len(diag) > 0)

    table = CompareAnalyzer.compare_table(results)
    check("CP-04", "对比表格", "总收益%" in table)

def test_report():
    base = StrategyParam(name="基准", min_score=75)
    results = [BacktestResult(strategy_name="A", params=base, total_trades=50, win_rate=55, sharpe=1.2)]
    report = ReportGenerator.generate_ranked_report(results)
    check("RP-01", "报告生成", "排名" in report)

    scan = ParameterScanner.scan(base, "min_score")
    scan = ParamScanResult(param_name="min_score", param_values=[70,75,80], results=[BacktestResult(strategy_name="A", params=base), BacktestResult(strategy_name="A", params=base), BacktestResult(strategy_name="A", params=base)])
    report2 = ReportGenerator.generate_param_report(scan)
    check("RP-02", "参数报告生成", "min_score" in report2)

def test_task_manager():
    clean()
    tm = TaskManager()
    s = [StrategyParam(name=f"策略_{i}") for i in range(3)]
    tid = tm.create_task("测试任务", s)
    check("TM-01", "创建任务", tid is not None)
    check("TM-02", "任务列表", len(tm.list_tasks()) == 1)

    task = tm.run_task(tid)
    check("TM-03", "执行完成", task.status == TaskStatus.COMPLETED)
    check("TM-04", "全部完成", task.completed_items == 3)
    check("TM-05", "有结果", len(task.results) == 3)

    # 缓存
    cached = tm.get_cached_result(tid, 0)
    check("TM-06", "缓存结果存在", cached is not None)

    # 取消
    tid2 = tm.create_task("可取消", s)
    check("TM-07", "取消成功", tm.cancel_task(tid2))
    check("TM-08", "状态已取消", tm.get_task(tid2).status == TaskStatus.CANCELLED)

def test_param_scan_task():
    clean()
    tm = TaskManager()
    base = StrategyParam(name="基准")
    tid = tm.create_task("参数扫描", [base])
    task = tm.run_param_scan(tid, base, ["min_score"])
    check("PST-01", "扫描完成", task.status == TaskStatus.COMPLETED)
    check("PST-02", "有扫描结果", len(task.param_scans) > 0)

def test_workbench():
    clean()
    wb = BatchWorkbench()
    strategies = [StrategyParam(name=f"策略_{i}") for i in range(3)]
    task = wb.run_batch(strategies)
    check("WB-01", "工作台批量回测", task.status == TaskStatus.COMPLETED)
    check("WB-02", "有结果", len(task.results) == 3)

    # 报告
    paths = wb.generate_reports(task.task_id)
    check("WB-03", "报告生成", len(paths) > 0)
    check("WB-04", "报告文件存在", all(os.path.exists(str(p)) for p in paths))

    # 参数扫描
    base = StrategyParam(name="基准")
    task2 = wb.run_param_scan(base, ["hold_days"])
    check("WB-05", "工作台参数扫描", task2.status == TaskStatus.COMPLETED)

def test_composite_score():
    r1 = BacktestResult(strategy_name="A", params=StrategyParam(name="A"),
                         sharpe=2.0, win_rate=60, max_drawdown=-5,
                         profit_factor=3.0, payoff_ratio=2.5, total_return_pct=30)
    r2 = BacktestResult(strategy_name="B", params=StrategyParam(name="B"),
                         sharpe=0.5, win_rate=35, max_drawdown=-30,
                         profit_factor=0.8, payoff_ratio=0.5, total_return_pct=-5)
    s1 = ParameterScanner._calc_composite(r1)
    s2 = ParameterScanner._calc_composite(r2)
    check("CS-01", "优秀策略高分", s1 > s2)


if __name__ == "__main__":
    tests = [
        test_data_model, test_simulator, test_param_scan,
        test_compare, test_report, test_task_manager,
        test_param_scan_task, test_workbench, test_composite_score,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            import traceback
            print(f"❌ {t.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'='*40}")
    print(f"  测试结果: {PASS}/{PASS+FAIL} 通过")
    if FAIL:
        print(f"  ❌ {FAIL} 个失败")
    else:
        print(f"  ✅ 全部通过")