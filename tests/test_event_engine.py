#!/usr/bin/env python3
"""事件驱动引擎 — 全量测试"""
import sys, os, math, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'agents'))
from pathlib import Path
from event_engine import (
    EventRecord, EventConfig, EventScorer, DataSource,
    EarningsSurpriseDetector, CombinedEventEngine
)
from event_backtest import (
    BacktestEngine, BacktestSummary, SignalGenerator, TradeSignal,
    EventPoolBridge, PositionManager, RiskManager
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = 0, 0
results = []

def check(case_id, name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; results.append((case_id, name, "✅", ""))
    else:
        FAIL += 1; results.append((case_id, name, "❌", detail))

def test_scoring():
    """测试评分引擎"""
    # EV-01 财报超预期
    stock = {"rev_yoy": 35, "np_yoy": 60, "np_qoq": 25, "roe": 18}
    s, sig = EventScorer.score_earnings_surprise(stock)
    check("SC-01", "EV-01评分>60", s >= 60)
    check("SC-01b", "EV-01有信号", len(sig) >= 2)

    # EV-02 动量突破
    kline = [{"close": str(90 + i * 0.3), "volume": str(1000 + i * 10)} for i in range(80)]
    s, sig = EventScorer.score_momentum_breakout({}, kline)
    check("SC-02", "EV-02评分>30", s >= 30)

    # EV-03 成交量异常（最后1天是前5天的3倍）
    kline_v = [{"close": "100", "volume": str(1000)} for _ in range(75)] + \
              [{"close": "100", "volume": str(1000)} for _ in range(4)] + \
              [{"close": "105", "volume": "5000"}]  # 最后1天巨量
    s, sig = EventScorer.score_volume_surge({"chg_pct": 4}, kline_v)
    check("SC-03", "EV-03放量检测", s >= 30)
    check("SC-03b", "EV-03量价信号", "量价齐升" in str(sig) or "放量" in str(sig))

    # EV-04 净利润断层
    kline_gap = [{"close": "100", "open": "100"}, {"close": "108", "open": "105"}]
    s, sig = EventScorer.score_profit_gap({"np_yoy": 80, "chg_pct": 6, "roe": 20}, kline_gap)
    check("SC-04", "EV-04评分>50", s >= 50)

    # EV-05 超跌反弹
    kline_down = [{"close": str(100 - i * 2), "volume": str(5000 - i * 50)} for i in range(25)]
    s, sig = EventScorer.score_oversold_rebound({}, kline_down)
    check("SC-05", "EV-05超跌检测", s >= 30)

    # EV-06 高ROE增长
    s, sig = EventScorer.score_high_roe_growth(
        {"roe": 22, "rev_yoy": 25, "np_yoy": 40, "np_qoq": 15})
    check("SC-06", "EV-06评分>60", s >= 60)

    # EV-07 高送转预期
    s, sig = EventScorer.score_high_send_expect(
        {"undistributed_profit": 3.5, "capital_reserve": 4.0,
         "np_yoy": 30, "market_cap": 5e9})
    check("SC-07", "EV-07评分>50", s >= 50)

    # 边界测试
    s, sig = EventScorer.score_earnings_surprise({"rev_yoy": -5, "np_yoy": -10, "np_qoq": -5, "roe": 2})
    check("SC-B1", "差财报评分低", s < 30)

    s, sig = EventScorer.score_momentum_breakout({}, [])
    check("SC-B2", "空K线=0分", s == 0)

def test_event_record():
    """测试事件数据模型"""
    ev = EventRecord(
        event_id="TEST_001", event_type="EV-01", event_name="测试事件",
        code="600000", name="测试股票", trigger_date="2026-07-16",
        event_score=85, signals={"信号1": "值1"},
        raw_data={"pe": 15.0}
    )
    check("ER-01", "事件创建", ev.event_id == "TEST_001")
    check("ER-02", "评分正确", ev.event_score == 85)
    check("ER-03", "信号保留", "信号1" in ev.signals)

def test_signal_generator():
    """测试信号生成器"""
    configs = {"EV-01": EventConfig("EV-01", min_score=60, max_position_pct=5)}
    gen = SignalGenerator(configs)
    events = [
        EventRecord(event_id="SIG_001", event_type="EV-01", event_name="测试",
                     code="600000", name="测试A", trigger_date="2026-07-16", event_score=80),
        EventRecord(event_id="SIG_002", event_type="EV-01", event_name="测试",
                     code="600001", name="测试B", trigger_date="2026-07-16", event_score=50),
    ]
    signals = gen.generate(events)
    check("SG-01", "高评分生成信号", len(signals) == 1)
    check("SG-02", "低评分过滤", signals[0].code == "600000" if signals else True)
    check("SG-03", "信号含止损", signals[0].stop_loss > 0 if signals else True)

def test_backtest_engine():
    """测试回测引擎"""
    engine = BacktestEngine()
    events = [
        EventRecord(event_id=f"BT_{i:04d}", event_type="EV-01", event_name="测试",
                     code=f"600{i:04d}", name=f"股票{i}",
                     trigger_date="2026-07-16", event_score=70 + i * 5)
        for i in range(20)
    ]
    configs = {"EV-01": EventConfig("EV-01", min_score=60)}
    summary = engine.run_backtest(events, configs, hold_days=5)
    check("BT-01", "回测有交易", summary.total_trades > 0)
    check("BT-02", "胜率计算", 20 <= summary.win_rate <= 80)
    check("BT-03", "总收益计算", isinstance(summary.total_return_pct, float))
    check("BT-04", "夏普计算", isinstance(summary.sharpe, (int, float)))
    check("BT-05", "衰减分析", len(summary.decay_analysis) > 0)

def test_pool_bridge():
    """测试五池对接"""
    bridge = EventPoolBridge()
    events = [
        EventRecord(event_id="BR_001", event_type="EV-01", event_name="测试",
                     code="600000", name="股票A", trigger_date="2026-07-16",
                     event_score=88, event_strength=85,
                     signals={"信号1": "值1", "信号2": "值2"}),
        EventRecord(event_id="BR_002", event_type="EV-02", event_name="测试",
                     code="600001", name="股票B", trigger_date="2026-07-16",
                     event_score=72, event_strength=70,
                     signals={"信号3": "值3"}),
    ]
    report = bridge.generate_signal_report(events)
    check("PB-01", "报告含事件数", "2" in report)
    check("PB-02", "报告含评分", "88" in report)

    pool = bridge.to_fast_screen_pool(events)
    check("PB-03", "候选池格式", len(pool) == 2)
    check("PB-04", "候选池字段", "股票代码" in pool[0])
    check("PB-05", "低分过滤", len(bridge.to_fast_screen_pool(events, min_score=80)) == 1)

def test_detector():
    """测试事件检测器"""
    ds = DataSource()
    detector = EarningsSurpriseDetector(ds)
    stocks = [
        {"f12": "600000", "f14": "测试A", "f37": 25, "f41": 80, "f46": 150,
         "f48": 50, "f40": 1e9, "f45": 1e8, "f100": "银行", "f20": 1e10,
         "f25": 2.5, "f9": 15, "f23": 2, "f3": 10, "f38": 3,
         "f115": 3.5, "f152": 4.0},
    ]
    events = detector.detect(stocks)
    check("DT-01", "高增长触发事件", len(events) >= 1)
    if events:
        check("DT-02", "事件ID格式", events[0].event_id.startswith("EV01"))
        check("DT-03", "评分输出", events[0].event_score > 0)

def test_event_config():
    """测试事件配置"""
    cfg = EventConfig("EV-01", min_score=65, max_position_pct=5,
                       stop_loss_pct=-5, take_profit_pct=12, hold_days=5)
    check("CF-01", "配置类型", cfg.event_type == "EV-01")
    check("CF-02", "最小评分", cfg.min_score == 65)
    check("CF-03", "止损", cfg.stop_loss_pct == -5)
    check("CF-04", "默认启用", cfg.enabled == True)

def test_position_manager():
    """测试持仓管理"""
    pm = PositionManager()
    sig = TradeSignal(event_id="PM_001", code="600000", name="测试",
                       signal_date="2026-07-16", entry_price=100,
                       stop_loss=95, take_profit=110)
    pm.open_position(sig)
    check("PM-01", "开仓成功", len(pm.positions) == 1)
    check("PM-02", "状态变化", sig.status == "entered")

    # 止盈测试
    closed = pm.update_prices({"600000": 112}, "2026-07-17")
    check("PM-03", "止盈触发", len(closed) == 1)
    check("PM-04", "止盈原因", closed[0].exit_reason == "take_profit" if closed else False)
    check("PM-05", "平仓后空仓", len(pm.positions) == 0)

def test_risk_manager():
    """测试风险管理"""
    rm = RiskManager(max_positions=3)
    sig = TradeSignal(event_id="RM_001", code="600000", name="测试", signal_date="2026-07-16")
    ok, _ = rm.check_entry(sig, 2)
    check("RM-01", "未超限可开仓", ok == True)
    ok2, _ = rm.check_entry(sig, 3)
    check("RM-02", "超限不可开仓", ok2 == False)

def test_backtest_summary():
    """测试回测汇总"""
    s = BacktestSummary(
        event_type="EV-01", total_trades=100, win_count=55, loss_count=45,
        total_return_pct=25.5, avg_return_pct=0.25, win_rate=55.0,
        profit_factor=1.8, avg_win_pct=3.5, avg_loss_pct=-1.2,
        sharpe=1.5, max_drawdown=-8.0, calmar=0.5,
    )
    check("BS-01", "汇总初始化", s.total_trades == 100)
    check("BS-02", "胜率正确", s.win_rate == 55.0)
    check("BS-03", "盈亏比>0", s.profit_factor > 0)
    check("BS-04", "夏普合理", 0 < s.sharpe < 5)


def test_event_scorer_stress():
    """评分引擎压力测试（10种组合）"""
    scenarios = [
        ("理想", {"rev_yoy": 80, "np_yoy": 150, "np_qoq": 60, "roe": 28}, 90),
        ("良好", {"rev_yoy": 35, "np_yoy": 60, "np_qoq": 20, "roe": 18}, 65),
        ("一般", {"rev_yoy": 15, "np_yoy": 20, "np_qoq": 5, "roe": 10}, 5),
        ("亏损", {"rev_yoy": -10, "np_yoy": -30, "np_qoq": -15, "roe": -5}, 0),
    ]
    for name, data, expected_min in scenarios:
        s, _ = EventScorer.score_earnings_surprise(data)
        check(f"SC-S{name}", f"EV-01-{name}评分>={expected_min}", s >= expected_min)


# ── 入口 ──
if __name__ == "__main__":
    tests = [
        test_event_record, test_event_config, test_scoring,
        test_detector, test_signal_generator, test_backtest_engine,
        test_pool_bridge, test_position_manager, test_risk_manager,
        test_backtest_summary, test_event_scorer_stress,
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
        for cid, name, st, d in results:
            if st != "✅":
                print(f"    {cid} {name}: {d}")
    else:
        print(f"  ✅ 全部通过")
    # 统计分类
    cats = {}
    for cid, _, st, _ in results:
        prefix = cid.split("-")[0]
        cats.setdefault(prefix, {"p": 0, "t": 0})
        cats[prefix]["t"] += 1
        if st == "✅": cats[prefix]["p"] += 1
    print(f"\n  分类:")
    for k, v in sorted(cats.items()):
        print(f"    {k}: {v['p']}/{v['t']}")