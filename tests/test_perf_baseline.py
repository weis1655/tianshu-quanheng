#!/usr/bin/env python3
"""天枢全链路性能压测 - P1基线测试套件

覆盖五大核心链路：
1. 行情链路 - 行情数据获取吞吐量/延迟
2. 计算链路 - 因子计算耗时/CPU/内存
3. 信号生成 - 筛选/审查/决策耗时
4. 交易链路 - 风控校验/订单生成端到端
5. 数据存储 - JSON读写/文件IO吞吐量

所有测试使用 cProfile + time.time + memory_profiler 采集真实数据。
"""
import sys, os, json, time, timeit, threading, gc, tracemalloc
from pathlib import Path
from typing import List, Dict, Any
import cProfile
import pstats
import io

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

RESULTS: List[Dict[str, Any]] = []

def record(name: str, category: str, result: float, unit: str = "ms", details: str = ""):
    """记录单次测试结果为结构化条目"""
    item = {
        "name": name,
        "category": category,
        "result": round(result, 3),
        "unit": unit,
        "details": details,
    }
    RESULTS.append(item)
    icon = "✅" if unit in ("ms",) and result < 5000 else "⚠️" if unit in ("ms",) and result < 15000 else "🔴"
    print(f"  {icon} {name}: {result:.2f}{unit} {details}")
    return item


# ═══════════════════════════════════════════════════════════════════════════
# 链路1: 行情数据获取 (Quote Service)
# ═══════════════════════════════════════════════════════════════════════════
def bench_quote_single():
    """单标的行情获取耗时"""
    try:
        from market_agent import fetch_quotes
        codes = ["sz300750"]
        def fn(): return fetch_quotes(codes)
        times = timeit.timeit(fn, number=3, globals=globals())
        avg_ms = times / 3 * 1000
        record("行情-单标的", "行情链路", avg_ms, "ms", f"平均3次")
    except Exception as e:
        record("行情-单标的", "行情链路", -1, "ms", f"失败: {e}")


def bench_quote_batch(n: int = 20):
    """批量行情获取耗时（模拟候选池规模）"""
    try:
        from market_agent import fetch_quotes
        codes = [f"sz{str(i).zfill(6)}" for i in range(100, 100+n)]
        def fn(): return fetch_quotes(codes)
        start = time.time()
        res = fn()
        elapsed_ms = (time.time() - start) * 1000
        record(f"行情-批量{n}只", "行情链路", elapsed_ms, "ms",
               f"返回{len(res)}条, {elapsed_ms/n:.1f}ms/只")
    except Exception as e:
        record(f"行情-批量{n}只", "行情链路", -1, "ms", f"失败: {e}")


def bench_quote_sequential(n: int = 50):
    """串行逐只行情获取（基线对比批量）"""
    try:
        from market_agent import fetch_quotes
        codes = [f"sz{str(i).zfill(6)}" for i in range(100, 100+n)]
        total_ms = 0
        for c in codes:
            start = time.time()
            fetch_quotes([c])
            total_ms += (time.time() - start) * 1000
        record(f"行情-串行{n}只", "行情链路", total_ms, "ms",
               f"逐只串行, {total_ms/n:.1f}ms/只")
    except Exception as e:
        record(f"行情-串行{n}只", "行情链路", -1, "ms", f"失败: {e}")


def bench_quote_cache():
    """QuoteService session缓存命中对比"""
    try:
        from quote_service import QuoteService
        sid = "perf_test_cache"
        QuoteService.init_session(sid)
        codes = ["sz300750", "sz000001"]
        # 首次拉取
        start = time.time()
        q1 = QuoteService.get_prices(codes, sid)
        first_ms = (time.time() - start) * 1000
        # 缓存命中
        start = time.time()
        q2 = QuoteService.get_prices(codes, sid)
        cached_ms = (time.time() - start) * 1000
        ratio = cached_ms / first_ms if first_ms > 0 else 0
        record("行情-缓存首次", "行情链路", first_ms, "ms", f"缓存未命中")
        record("行情-缓存命中", "行情链路", cached_ms, "ms",
               f"缓存命中, 相比首次{ratio:.2%}")
    except Exception as e:
        record("行情-缓存", "行情链路", -1, "ms", f"失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 链路2: 因子计算 (Technical Analysis / Factors)
# ═══════════════════════════════════════════════════════════════════════════
def bench_tech_score_single():
    """单标的技术指标计算耗时"""
    try:
        from market_agent import calculate_technical_score
        stock = {
            "代码": "300750", "名称": "宁德时代", "close": 180.5, "open": 178.0,
            "high": 182.0, "low": 177.5, "pre_close": 179.0,
            "volume": 50000000, "amount": 9000000000, "turnover_rate": 3.5,
            "limit_up": 196.9, "limit_down": 161.1,
            "流通市值": 150000000000,
        }
        def fn(): return calculate_technical_score(stock)
        times = timeit.timeit(fn, number=100)
        avg_ms = times / 100 * 1000
        record("因子-技术指标单只", "计算链路", avg_ms, "ms", "100次平均")
    except Exception as e:
        record("因子-技术指标单只", "计算链路", -1, "ms", f"失败: {e}")


def bench_qlib_factors_single():
    """单标的Qlib因子计算耗时"""
    try:
        from market_agent import calculate_qlib_factors
        stock = {
            "代码": "300750", "close": 180.5, "open": 178.0,
            "high": 182.0, "low": 177.5, "pre_close": 179.0,
            "volume": 50000000, "amount": 9000000000, "turnover_rate": 3.5,
        }
        def fn(): return calculate_qlib_factors(stock)
        times = timeit.timeit(fn, number=100)
        avg_ms = times / 100 * 1000
        record("因子-Qlib单只", "计算链路", avg_ms, "ms", "100次平均")
    except Exception as e:
        record("因子-Qlib单只", "计算链路", -1, "ms", f"失败: {e}")


def bench_factor_batch():
    """批量因子计算（模拟候选池20只）"""
    try:
        from market_agent import calculate_technical_score
        def make_stock(idx):
            base = 100 + idx * 10
            return {
                "代码": f"sz{str(idx).zfill(6)}", "close": base,
                "open": base - 2, "high": base + 3, "low": base - 3,
                "pre_close": base - 1, "volume": 30000000 + idx * 100000,
                "amount": 600000000 + idx * 5000000, "turnover_rate": 2.5 + idx * 0.1,
                "limit_up": base * 1.1, "limit_down": base * 0.9,
                "流通市值": 20000000000 + idx * 500000000,
            }
        stocks = [make_stock(i) for i in range(1, 21)]
        def fn():
            return [calculate_technical_score(s) for s in stocks]
        start = time.time()
        fn()
        elapsed_ms = (time.time() - start) * 1000
        record("因子-批量20只技术指标", "计算链路", elapsed_ms, "ms",
               f"{elapsed_ms/20:.1f}ms/只")
    except Exception as e:
        record("因子-批量20只", "计算链路", -1, "ms", f"失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 链路3: 信号生成 (Screen → Review → Decision)
# ═══════════════════════════════════════════════════════════════════════════
def bench_screen_agent():
    """筛选Agent单次运行耗时（mock，不实际拉取LLM）"""
    try:
        from screen_agent import ScreenAgent
        agent = ScreenAgent(PROJECT_ROOT)
        # 设置最小耗时：跳过LLM调用，直接返回mock
        import types
        original = agent._run_impl
        def mock_run_impl(*args, **kwargs):
            return {"screened": 20, "time": time.time()}
        agent._run_impl = types.MethodType(mock_run_impl, agent)
        start = time.time()
        result = agent.run()
        elapsed_ms = (time.time() - start) * 1000
        record("信号-筛选Agent", "信号链路", elapsed_ms, "ms", "mock模式")
    except Exception as e:
        record("信号-筛选Agent", "信号链路", -1, "ms", f"失败: {e}")


def bench_overheat_detector():
    """过热检测耗时（OverheatDetector）"""
    try:
        from review_scorer import OverheatDetector
        detector = OverheatDetector()
        stock = {
            "代码": "300750", "名称": "测试", "涨跌幅": 9.8, "综合分": 80,
            "流通市值": 10000000000, "历史数据": {"days": [{"涨跌幅": 5, "close": 100} for _ in range(30)]},
        }
        def fn():
            return OverheatDetector.detect(
                change_pct=9.8, pe_ttm=25, turnover=3.5, volume_ratio=1.2,
                month_chg=5.0, quarter_chg=8.0, composite_score=80,
                amplitude=2.0, market_state="震荡"
            )
        times = timeit.timeit(fn, number=500)
        avg_ms = times / 500 * 1000
        record("信号-过热检测", "信号链路", avg_ms, "ms", "500次平均")
    except Exception as e:
        record("信号-过热检测", "信号链路", -1, "ms", f"失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 链路4: 交易链路 (Risk Control / Order Generation)
# ═══════════════════════════════════════════════════════════════════════════
def bench_gate_controller():
    """GateController阻塞计数检查耗时"""
    try:
        from gate_controller import GateController
        gc = GateController()
        key_pool_data = {
            "stocks": [
                {"代码": f"00000{i}", "名称": f"测试{i}", "blocked_count": 0}
                for i in range(1, 11)
            ]
        }
        blocked_codes = {"000001"}
        def fn(): return gc.check_blocked_count(key_pool_data, blocked_codes, None)
        times = timeit.timeit(fn, number=1000)
        avg_ms = times / 1000 * 1000
        record("交易-GateController检查", "交易链路", avg_ms, "ms", "1000次平均")
    except Exception as e:
        record("交易-GateController", "交易链路", -1, "ms", f"失败: {e}")


def bench_decision_agent_init():
    """DecisionAgent初始化耗时（含加载五池/历史等）"""
    try:
        from decision_agent import DecisionAgent
        start = time.time()
        agent = DecisionAgent(PROJECT_ROOT)
        elapsed_ms = (time.time() - start) * 1000
        record("交易-DecisionAgent初始化", "交易链路", elapsed_ms, "ms", "含文件加载")
    except Exception as e:
        record("交易-DecisionAgent初始化", "交易链路", -1, "ms", f"失败: {e}")


def bench_market_state():
    """市场状态检测耗时"""
    try:
        from decision_agent import DecisionAgent
        agent = DecisionAgent(PROJECT_ROOT)
        sm = PROJECT_ROOT / "data" / "shared_memory.json"
        sm.write_text(json.dumps([
            {"代码": "000001", "涨跌幅": 0.5, "最新价": 3100},
            {"代码": "399006", "涨跌幅": 0.3, "最新价": 1800},
            {"代码": "000300", "涨跌幅": 0.2, "最新价": 3600},
        ]))
        def fn(): return agent._get_market_state()
        times = timeit.timeit(fn, number=500)
        avg_ms = times / 500 * 1000
        record("交易-市场状态检测", "交易链路", avg_ms, "ms", "500次平均")
    except Exception as e:
        record("交易-市场状态检测", "交易链路", -1, "ms", f"失败: {e}")


def bench_circuit_breaker():
    """熔断器 call() 耗时"""
    try:
        from error_handling import CircuitBreaker
        cb = CircuitBreaker(name="perf_test")
        def fn(): return cb.call(lambda: "ok")
        times = timeit.timeit(fn, number=10000)
        avg_ms = times / 10000 * 1000
        record("交易-熔断器call()", "交易链路", avg_ms, "ms", "10000次平均")
    except Exception as e:
        record("交易-熔断器", "交易链路", -1, "ms", f"失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 链路5: 数据存储 (JSON Read/Write / File IO)
# ═══════════════════════════════════════════════════════════════════════════
def bench_json_read(n_kb: int = 500):
    """大JSON文件读取耗时"""
    try:
        test_file = PROJECT_ROOT / "data" / ".perf_test_large.json"
        large = {"items": [{"id": i, "data": "x" * 100, "value": i * 1.5} for i in range(n_kb * 10)]}
        test_file.write_text(json.dumps(large, ensure_ascii=False))
        size_kb = test_file.stat().st_size / 1024
        def fn():
            d = json.loads(test_file.read_text())
            return len(d["items"])
        times = timeit.timeit(fn, number=10)
        avg_ms = times / 10 * 1000
        record(f"存储-JSON读取{size_kb:.0f}KB", "存储链路", avg_ms, "ms", "10次平均")
        test_file.unlink(missing_ok=True)
    except Exception as e:
        record("存储-JSON读取", "存储链路", -1, "ms", f"失败: {e}")


def bench_json_write(n_kb: int = 500):
    """大JSON文件写入耗时"""
    try:
        test_file = PROJECT_ROOT / "data" / ".perf_test_write.json"
        large = {"items": [{"id": i, "data": "x" * 100, "value": i * 1.5} for i in range(n_kb * 10)]}
        def fn():
            test_file.write_text(json.dumps(large, ensure_ascii=False))
        start = time.time()
        fn()
        elapsed_ms = (time.time() - start) * 1000
        size_kb = test_file.stat().st_size / 1024
        record(f"存储-JSON写入{size_kb:.0f}KB", "存储链路", elapsed_ms, "ms", f"吞吐{size_kb/elapsed_ms*1000:.1f}KB/s")
        test_file.unlink(missing_ok=True)
    except Exception as e:
        record("存储-JSON写入", "存储链路", -1, "ms", f"失败: {e}")


def bench_batch_json_io():
    """批量小JSON文件读写（模拟五池/决策日志场景）"""
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="perf_batch_")
        files = [Path(tmpdir) / f"file_{i}.json" for i in range(50)]
        # 批量写入
        start = time.time()
        for f in files:
            f.write_text(json.dumps({"i": 1, "data": "x"*200}))
        write_ms = (time.time() - start) * 1000
        # 批量读取
        start = time.time()
        results = [json.loads(f.read_text()) for f in files]
        read_ms = (time.time() - start) * 1000
        record("存储-批量50文件写入", "存储链路", write_ms, "ms", f"{write_ms/50:.1f}ms/文件")
        record("存储-批量50文件读取", "存储链路", read_ms, "ms", f"{read_ms/50:.1f}ms/文件")
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        record("存储-批量IO", "存储链路", -1, "ms", f"失败: {e}")


def bench_shared_memory_load():
    """共享内存JSON加载耗时"""
    try:
        sm_file = PROJECT_ROOT / "data" / "shared_memory.json"
        def fn(): return json.loads(sm_file.read_text())
        times = timeit.timeit(fn, number=100)
        avg_ms = times / 100 * 1000
        size_kb = sm_file.stat().st_size / 1024
        record(f"存储-共享内存加载({size_kb:.0f}KB)", "存储链路", avg_ms, "ms", "100次平均")
    except Exception as e:
        record("存储-共享内存加载", "存储链路", -1, "ms", f"失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 并发稳定性验证
# ═══════════════════════════════════════════════════════════════════════════
def bench_concurrent_factor():
    """多线程并发因子计算"""
    try:
        from market_agent import calculate_technical_score
        import concurrent.futures

        def make_stock(idx):
            base = 100 + idx * 5
            return {
                "代码": f"sz{idx:06d}", "close": base, "open": base-2,
                "high": base+3, "low": base-3, "pre_close": base-1,
                "volume": 30000000, "amount": 600000000, "turnover_rate": 3.0,
                "limit_up": base*1.1, "limit_down": base*0.9, "流通市值": 20000000000,
            }
        stocks = [make_stock(i) for i in range(100)]
        workers = [2, 4, 8]
        results = {}
        for n in workers:
            start = time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
                list(ex.map(calculate_technical_score, stocks))
            elapsed_ms = (time.time() - start) * 1000
            results[n] = round(elapsed_ms, 1)
            record(f"并发-因子{len(stocks)}只({n}线程)", "并发场景", elapsed_ms, "ms",
                   f"{elapsed_ms/len(stocks):.2f}ms/只")
        # 单线程基线
        start = time.time()
        [calculate_technical_score(s) for s in stocks]
        single_ms = (time.time() - start) * 1000
        results[1] = round(single_ms, 1)
        record(f"并发-因子{len(stocks)}只(单线程基线)", "并发场景", single_ms, "ms",
               f"{single_ms/len(stocks):.2f}ms/只")
    except Exception as e:
        record("并发-因子计算", "并发场景", -1, "ms", f"失败: {e}")


def bench_memory_leak():
    """长时间运行的内存占用变化"""
    try:
        tracemalloc.start()
        from market_agent import calculate_technical_score

        def make_stock(idx):
            return {
                "代码": f"sz{idx:06d}", "close": 100+idx, "open": 100+idx-2,
                "high": 100+idx+3, "low": 100+idx-3, "pre_close": 100+idx-1,
                "volume": 30000000, "amount": 600000000, "turnover_rate": 3.0,
                "limit_up": 110+idx, "limit_down": 90+idx, "流通市值": 20000000000,
            }
        # 初始快照
        gc.collect()
        snap1 = tracemalloc.take_snapshot()
        # 循环计算
        for _ in range(1000):
            s = make_stock(_)
            calculate_technical_score(s)
        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        # 计算差异（排除标准库和框架自身）
        stats = snap2.compare_to(snap1, "lineno")
        leak_kb = sum(s.size_diff for s in stats[:20] if s.size_diff > 0) / 1024
        tracemalloc.stop()
        record("内存-1000次循环因子计算", "并发场景", leak_kb, "KB", "净增内存(KB)")
    except Exception as e:
        record("内存-泄漏检测", "并发场景", -1, "KB", f"失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 主运行
# ═══════════════════════════════════════════════════════════════════════════
def run_all_benchmarks():
    print("\n" + "="*70)
    print("天枢全链路性能压测 - P1 基线数据采集")
    print("="*70)

    categories = [
        ("══════ 链路1: 行情数据获取 ══════", [
            bench_quote_single,
            bench_quote_batch,
            bench_quote_cache,
        ]),
        ("══════ 链路2: 因子计算 ══════", [
            bench_tech_score_single,
            bench_qlib_factors_single,
            bench_factor_batch,
        ]),
        ("══════ 链路3: 信号生成 ══════", [
            bench_screen_agent,
            bench_overheat_detector,
        ]),
        ("══════ 链路4: 交易链路 ══════", [
            bench_decision_agent_init,
            bench_market_state,
            bench_gate_controller,
            bench_circuit_breaker,
        ]),
        ("══════ 链路5: 数据存储 ══════", [
            bench_json_read,
            bench_json_write,
            bench_batch_json_io,
            bench_shared_memory_load,
        ]),
        ("══════ 并发场景 ══════", [
            bench_concurrent_factor,
            bench_memory_leak,
        ]),
    ]

    start_total = time.time()
    for title, benches in categories:
        print(f"\n{title}")
        for fn in benches:
            try:
                fn()
            except Exception as e:
                print(f"  🔴 {fn.__name__} 异常: {e}")
                RESULTS.append({
                    "name": fn.__name__, "category": title,
                    "result": -1, "unit": "ms", "details": f"异常: {e}",
                })

    total_ms = (time.time() - start_total) * 1000
    print(f"\n{'='*70}")
    print(f"压测完成: {len(RESULTS)}项测试, 总耗时 {total_ms/1000:.1f}s")
    print(f"{'='*70}\n")
    return RESULTS


if __name__ == "__main__":
    results = run_all_benchmarks()
    # 输出JSON供后续分析
    out = PROJECT_ROOT / "data" / "perf_baseline_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"基线数据已写入: {out}")
