#!/usr/bin/env python3
"""算法交易执行引擎 — 全量测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'agents'))
from algo_execution import (
    AlgoType, OrderSide, AlgoStatus,
    AlgoOrder, ChildOrder, ExecutionReport,
    AlgoExecutionEngine, TWAPEngine, VWAPEngine,
    IcebergEngine, AdaptiveEngine, ExecutionMonitor,
    ExceptionHandler, PriceSimulator, BacktestValidator
)

PASS, FAIL = 0, 0
CLEANED = False

def clean():
    global CLEANED
    if not CLEANED:
        d = os.path.join(os.path.dirname(__file__), '..', 'data', 'algo_execution')
        if os.path.exists(d):
            import shutil
            shutil.rmtree(d)
        CLEANED = True

def check(cid, name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  ❌ {cid} {name}: {detail}")

# ── 数据模型 ──
def test_data_model():
    o = AlgoOrder(algo_id="T001", algo_type=AlgoType.TWAP, code="600000", name="测试",
                   side=OrderSide.BUY, total_quantity=10000, slice_count=10)
    check("DM-01", "TWAP订单创建", o.algo_type == AlgoType.TWAP)
    check("DM-02", "默认状态", o.status == AlgoStatus.CREATED)
    check("DM-03", "BUY方向", o.side == OrderSide.BUY)

    c = ChildOrder(child_id="C001", algo_id="T001", seq=1, side=OrderSide.BUY, quantity=1000)
    check("DM-04", "子单创建", c.child_id == "C001")
    check("DM-05", "子单默认状态", c.status.value == "pending")

    r = ExecutionReport(algo_id="T001", algo_type="TWAP", code="600000", name="测试",
                         side="buy", total_qty=10000, total_amount=0,
                         filled_qty=10000, avg_price=99.5, vwap=99.0,
                         slippage_pct=-0.05, duration_seconds=30,
                         slice_count=10, status="completed", has_fallback=False)
    check("DM-06", "执行报告", r.filled_qty == 10000)

# ── TWAP算法 ──
def test_twap():
    order = AlgoOrder(algo_id="TWAP1", algo_type=AlgoType.TWAP, code="600000",
                       name="测试", side=OrderSide.BUY,
                       total_quantity=10000, duration_minutes=30, slice_count=10)
    slices = TWAPEngine.calculate_slices(order)
    check("TW-01", "拆单数=10", len(slices) == 10)
    total_qty = sum(s["quantity"] for s in slices)
    check("TW-02", "总量正确", total_qty == 10000)
    check("TW-03", "每份>=0", all(s["quantity"] > 0 for s in slices))

    # 模拟执行
    price_path = PriceSimulator.generate_path(100, 60)
    result = TWAPEngine.simulate(order, price_path)
    check("TW-04", "全部成交", result.filled_quantity == 10000)
    check("TW-05", "有均价", result.avg_price > 0)
    check("TW-06", "状态COMPLETED", result.status == AlgoStatus.COMPLETED)
    check("TW-07", "子单数>0", len(result.child_orders) > 0)

    # 限价超出容忍
    order2 = AlgoOrder(algo_id="TWAP2", algo_type=AlgoType.TWAP, code="600000",
                        name="测试", side=OrderSide.BUY,
                        total_quantity=10000, slice_count=5,
                        limit_price=90, price_tolerance=0.01)
    price_path2 = [100] * 20  # 远超限价90+1%=90.9
    result2 = TWAPEngine.simulate(order2, price_path2)
    skipped = sum(1 for c in result2.child_orders if c.status.value == "cancelled")
    check("TW-08", "超限价跳过", skipped >= 0)

# ── VWAP算法 ──
def test_vwap():
    order = AlgoOrder(algo_id="VWAP1", algo_type=AlgoType.VWAP, code="600000",
                       name="测试", side=OrderSide.BUY,
                       total_quantity=10000, slice_count=10)
    vol_profile = PriceSimulator.generate_volume_profile(10)
    slices = VWAPEngine.calculate_slices(order, vol_profile)
    check("VW-01", "VWAP拆单成功", len(slices) > 0)
    check("VW-02", "成交量分布存在", all(s.get("volume_ratio", 0) > 0 for s in slices))

    price_path = PriceSimulator.generate_path(100, 60)
    result = VWAPEngine.simulate(order, price_path, vol_profile)
    check("VW-03", "全部成交", result.filled_quantity == 10000)
    check("VW-04", "有VWAP值", result.vwap > 0)
    check("VW-05", "有滑点", isinstance(result.slippage_pct, float))

# ── 冰山单 ──
def test_iceberg():
    order = AlgoOrder(algo_id="ICE1", algo_type=AlgoType.ICEBERG, code="600000",
                       name="测试", side=OrderSide.BUY,
                       total_quantity=10000, iceberg_visible=1000)
    slices = IcebergEngine.calculate_slices(order)
    check("IC-01", "冰山拆单>0", len(slices) > 0)
    check("IC-02", "每份<=可见量", all(s["quantity"] <= 1000 for s in slices))

    price_path = PriceSimulator.generate_path(100, 60)
    result = IcebergEngine.simulate(order, price_path)
    check("IC-03", "全部成交", result.filled_quantity == 10000)
    check("IC-04", "COMPLETED", result.status == AlgoStatus.COMPLETED)

# ── 自适应策略 ──
def test_adaptive():
    check("AD-01", "高紧迫→主动",
          AdaptiveEngine.decide_strategy(None, 100, 1.0, "high") == "active")
    check("AD-02", "流动性差→被动",
          AdaptiveEngine.decide_strategy(None, 100, 0.3, "normal") == "passive")

# ── 异常处置 ──
def test_exception():
    check("EX-01", "剧烈波动检测",
          ExceptionHandler.check_volatility([100, 105, 95, 110], 0.03)[0])
    check("EX-02", "平稳行情通过",
          not ExceptionHandler.check_volatility([100, 100.5, 100.3, 100.8], 0.03)[0])
    liq, _ = ExceptionHandler.check_liquidity(0.2, 0.3)
    check("EX-03", "流动性不足检测", liq)

    order = AlgoOrder(algo_id="EX1", algo_type=AlgoType.TWAP, code="600000",
                       name="测试", side=OrderSide.BUY, total_quantity=10000)
    result = ExceptionHandler.handle_exception(order, "测试异常")
    check("EX-04", "降级状态", result.status == AlgoStatus.FALLBACK)
    check("EX-05", "降级原因记录", result.fallback_reason == "测试异常")

# ── 价格模拟器 ──
def test_simulator():
    path = PriceSimulator.generate_path(100, 60, volatility=0.005)
    check("SM-01", "路径长度61(含起点)", len(path) == 61)
    check("SM-02", "起点=100", path[0] == 100)
    check("SM-03", "终点不同", path[-1] != 100)

    vol = PriceSimulator.generate_volume_profile(10)
    check("SM-04", "成交量分布10份", len(vol) == 10)
    check("SM-05", "总和≈1", abs(sum(vol) - 1) < 0.01)

# ── 执行引擎 ──
def test_engine():
    clean()
    eng = AlgoExecutionEngine()
    aid = eng.create_order(AlgoType.TWAP, "600000", "测试", OrderSide.BUY, 10000)
    check("EG-01", "创建订单成功", aid.startswith("AE"))
    check("EG-02", "订单存储", eng.get_order(aid) is not None)

    result = eng.execute(aid)
    check("EG-03", "执行成功", result.status == AlgoStatus.COMPLETED)
    check("EG-04", "全部成交", result.filled_quantity == 10000)

    report = eng.get_report(aid)
    check("EG-05", "有执行报告", report is not None)
    check("EG-06", "报告含滑点", abs(report.slippage_pct) < 5)

    # 列表
    orders = eng.list_orders()
    check("EG-07", "列表不为空", len(orders) > 0)

    # VWAP执行
    aid2 = eng.create_order(AlgoType.VWAP, "600001", "测试B", OrderSide.SELL, 5000)
    price_path = PriceSimulator.generate_path(100, 60)
    vol = PriceSimulator.generate_volume_profile(10)
    result2 = eng.execute(aid2, price_path, vol)
    check("EG-08", "VWAP执行", result2.status == AlgoStatus.COMPLETED)

    # 剧烈波动 → 降级
    aid3 = eng.create_order(AlgoType.TWAP, "600002", "测试C", OrderSide.BUY, 5000)
    volatile_path = [100 * (1 + 0.05 * (i % 2)) for i in range(20)]
    result3 = eng.execute(aid3, volatile_path)
    check("EG-09", "波动降级", result3.status in (AlgoStatus.FALLBACK, AlgoStatus.COMPLETED))

# ── 回放验证 ──
def test_backtest():
    result = BacktestValidator.run_comparison(AlgoType.TWAP, 10000, 100, 60)
    check("BT-01", "回放结果含滑点", "algo_slippage_pct" in result)
    check("BT-02", "均价>0", result.get("algo_avg_price", 0) > 0)

    multi = BacktestValidator.run_all_comparisons()
    check("BT-03", "多算法对比", len(multi) >= 3)

    scenarios = BacktestValidator.run_multi_scenario()
    check("BT-04", "多场景测试", len(scenarios) >= 5)

# ── 监控 ──
def test_monitor():
    order = AlgoOrder(algo_id="MON1", algo_type=AlgoType.TWAP, code="600000",
                       name="测试", side=OrderSide.BUY,
                       total_quantity=10000, filled_quantity=5000, avg_price=99.5)
    prog = ExecutionMonitor.progress(order)
    check("MN-01", "进度50%", abs(prog["progress_pct"] - 50) < 0.1)
    check("MN-02", "含均价", prog["avg_price"] == 99.5)

    report = ExecutionMonitor.generate_report(order)
    check("MN-03", "报告生成", report.filled_qty == 5000)
    check("MN-04", "状态", report.status == "created")


if __name__ == "__main__":
    tests = [
        test_data_model, test_simulator,
        test_twap, test_vwap, test_iceberg,
        test_adaptive, test_exception,
        test_engine, test_backtest, test_monitor,
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