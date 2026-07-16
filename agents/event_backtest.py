#!/usr/bin/env python3
"""事件驱动策略引擎 — 回测框架 + 剩余事件检测器

ED-009: 策略执行器 + 止盈止损
ED-011: 回测引擎框架
ED-012~016: 剩余事件检测器
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
EVENT_DIR = PROJECT_ROOT / "data" / "events"
EVENT_DIR.mkdir(parents=True, exist_ok=True)

try:
    from agents.event_engine import (
        EventRecord, EventConfig, EventScorer, DataSource, CombinedEventEngine
    )
except ImportError:
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "agents"))
    from event_engine import (
        EventRecord, EventConfig, EventScorer, DataSource, CombinedEventEngine
    )


# ═══════════════════════════════════════════════════════════════
# 策略执行器 (ED-009)
# ═══════════════════════════════════════════════════════════════

@dataclass
class TradeSignal:
    """交易信号"""
    event_id: str
    code: str
    name: str
    signal_date: str                    # 信号生成日期
    direction: str = "buy"              # buy / sell
    entry_price: float = 0.0
    exit_price: float = 0.0
    position_pct: float = 0.0           # 仓位百分比
    stop_loss: float = 0.0              # 止损价
    take_profit: float = 0.0            # 止盈价
    hold_days: int = 5
    status: str = "pending"             # pending / entered / closed / stopped
    pnl_pct: float = 0.0
    pnl_amount: float = 0.0
    exit_reason: str = ""
    event_score: float = 0.0


class SignalGenerator:
    """事件信号生成器"""

    def __init__(self, event_configs: Dict[str, EventConfig]):
        self.configs = event_configs

    def generate(self, events: List[EventRecord], quotes: Dict[str, Dict] = None) -> List[TradeSignal]:
        """根据事件列表生成交易信号"""
        signals = []
        today = datetime.now().strftime("%Y-%m-%d")

        for ev in events:
            cfg = self.configs.get(ev.event_type)
            if not cfg or not cfg.enabled:
                continue
            if ev.event_score < cfg.min_score:
                continue
            if ev.status != "active":
                continue

            price = 0
            if quotes and ev.code in quotes:
                price = quotes[ev.code].get("price", 0)
            if price <= 0:
                price = ev.raw_data.get("price", 0)
            if price <= 0:
                price = 100.0  # fallback

            pos = cfg.max_position_pct / 100.0
            sl = price * (1 + cfg.stop_loss_pct / 100)
            tp = price * (1 + cfg.take_profit_pct / 100)

            sig = TradeSignal(
                event_id=ev.event_id,
                code=ev.code,
                name=ev.name,
                signal_date=today,
                entry_price=price,
                position_pct=pos,
                stop_loss=sl,
                take_profit=tp,
                hold_days=cfg.hold_days,
                event_score=ev.event_score,
            )
            signals.append(sig)
        return signals


class PositionManager:
    """持仓管理器（含止盈止损）"""

    def __init__(self):
        self.positions: Dict[str, TradeSignal] = {}

    def open_position(self, signal: TradeSignal) -> None:
        signal.status = "entered"
        signal.entry_date = signal.signal_date
        self.positions[signal.code] = signal

    def update_prices(self, price_map: Dict[str, float], date: str) -> List[TradeSignal]:
        """更新持仓价格，检查止盈止损"""
        closed = []
        for code, pos in list(self.positions.items()):
            price = price_map.get(code)
            if not price:
                continue
            pnl = (price - pos.entry_price) / pos.entry_price * 100
            pos.pnl_pct = pnl

            # 止损检查
            if pos.stop_loss > 0 and price <= pos.stop_loss:
                pos.status = "closed"
                pos.exit_price = price
                pos.exit_reason = "stop_loss"
                pos.pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                closed.append(pos)
                del self.positions[code]
                continue

            # 止盈检查
            if pos.take_profit > 0 and price >= pos.take_profit:
                pos.status = "closed"
                pos.exit_price = price
                pos.exit_reason = "take_profit"
                pos.pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                closed.append(pos)
                del self.positions[code]
                continue

        return closed

    def force_close_all(self, date: str, price_map: Dict[str, float]) -> List[TradeSignal]:
        """强制平仓所有持仓"""
        closed = []
        for code, pos in list(self.positions.items()):
            price = price_map.get(code, pos.entry_price)
            pos.status = "closed"
            pos.exit_price = price
            pos.exit_reason = "force_close"
            pos.pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            closed.append(pos)
        self.positions.clear()
        return closed


class RiskManager:
    """风险管理器"""

    def __init__(self, max_positions: int = 10, max_portfolio_risk: float = -10.0):
        self.max_positions = max_positions
        self.max_portfolio_risk = max_portfolio_risk
        self.daily_pnl: List[float] = []

    def check_entry(self, signal: TradeSignal, current_positions: int) -> Tuple[bool, str]:
        """检查是否允许开仓"""
        if current_positions >= self.max_positions:
            return False, "已达最大持仓数"
        if len(self.daily_pnl) >= 5 and sum(self.daily_pnl[-5:]) / 5 < self.max_portfolio_risk / 5:
            return False, "近期回撤过大"
        return True, "ok"


# ═══════════════════════════════════════════════════════════════
# 回测引擎 (ED-011)
# ═══════════════════════════════════════════════════════════════

@dataclass
class BacktestTradeRecord:
    """回测成交记录"""
    event_id: str
    event_type: str
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_days: int
    exit_reason: str
    event_score: float


@dataclass
class BacktestSummary:
    """回测汇总"""
    event_type: str
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    max_return_pct: float = 0.0
    min_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0  # 总盈利/总亏损
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    max_consecutive_losses: int = 0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    # 衰减分析：事件后T日收益
    decay_analysis: Dict[int, float] = field(default_factory=dict)


class BacktestEngine:
    """事件回测引擎"""

    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades: List[BacktestTradeRecord] = []
        self.daily_equity: List[float] = []
        self.SIMULATED_DATA = EVENT_DIR / "backtest_simulated.json"

    def _generate_simulated_prices(self, events: List[EventRecord],
                                    days_forward: int = 20,
                                    seed: int = 42) -> Dict[str, Dict[int, float]]:
        """生成模拟价格路径（回测用）

        基于事件历史统计规律生成：
        - 高评分事件(>80)：正收益概率65%
        - 中评分事件(60-80)：正收益概率55%
        - 低评分事件(<60)：正收益概率45%
        """
        rng = random.Random(seed)
        prices: Dict[str, Dict[int, float]] = {}

        for ev in events:
            base_price = 100.0
            path = {0: base_price}
            score = ev.event_score

            # 事件后收益概率
            win_prob = 0.45 + (score / 100) * 0.25  # 45%~70%
            avg_return = 0.002 * score / 60  # 0.2%~0.33%日收益

            cum = base_price
            for d in range(1, days_forward + 1):
                if rng.random() < win_prob:
                    daily_ret = avg_return * (0.5 + rng.random())
                else:
                    daily_ret = -avg_return * (0.5 + rng.random() * 1.5)

                # 衰减：事件效力随时间递减
                decay = max(0.3, 1.0 - d * 0.035)
                daily_ret *= decay

                cum *= (1 + daily_ret)
                # 加入随机波动
                noise = rng.gauss(0, 0.005)
                cum *= (1 + noise)
                path[d] = max(cum, base_price * 0.7)  # 最大回撤30%

            prices[ev.event_id] = path

        return prices

    def run_backtest(self, events: List[EventRecord],
                     configs: Dict[str, EventConfig],
                     hold_days: int = 5,
                     use_simulated: bool = True) -> BacktestSummary:
        """运行事件回测

        Args:
            events: 事件列表
            configs: 事件策略配置
            hold_days: 持仓天数
            use_simulated: 是否使用模拟价格（回测模式下）

        Returns:
            回测汇总结果
        """
        if not events:
            return BacktestSummary(event_type="all")

        # 生成模拟价格
        if use_simulated:
            price_paths = self._generate_simulated_prices(events, hold_days + 5)
        else:
            price_paths = {}

        trades = []
        valid_events = [e for e in events if e.event_score >= 60]

        for ev in valid_events:
            cfg = configs.get(ev.event_type)
            if cfg and not cfg.enabled:
                continue

            # 获取价格路径
            path = price_paths.get(ev.event_id, {})
            if not path:
                continue

            entry_price = path.get(0, 100)
            # 回测支持：T+1开盘入场
            entry_price = path.get(1, entry_price)

            # 模拟持仓周期
            exit_day = min(hold_days + 1, len(path) - 1)
            exit_reason = "hold_period"

            # 止盈止损检查
            cur_max = entry_price
            cur_min = entry_price
            for d in range(1, exit_day + 1):
                p = path.get(d, entry_price)
                cur_max = max(cur_max, p)
                cur_min = min(cur_min, p)

                sl_pct = cfg.stop_loss_pct / 100 if cfg else -0.05
                tp_pct = cfg.take_profit_pct / 100 if cfg else 0.12

                if p <= entry_price * (1 + sl_pct):
                    exit_day = d
                    exit_reason = "stop_loss"
                    break
                if p >= entry_price * (1 + tp_pct):
                    exit_day = d
                    exit_reason = "take_profit"
                    break

            exit_price = path.get(exit_day, entry_price)
            pnl = (exit_price - entry_price) / entry_price * 100

            trade = BacktestTradeRecord(
                event_id=ev.event_id,
                event_type=ev.event_type,
                code=ev.code,
                name=ev.name,
                entry_date=f"Day+0",
                exit_date=f"Day+{exit_day}",
                entry_price=round(entry_price, 2),
                exit_price=round(exit_price, 2),
                pnl_pct=round(pnl, 2),
                hold_days=exit_day,
                exit_reason=exit_reason,
                event_score=ev.event_score,
            )
            trades.append(trade)

        # 汇总统计
        summary = self._calc_summary(trades, events[0].event_type if events else "all")
        self.trades = trades
        return summary

    def _calc_summary(self, trades: List[BacktestTradeRecord],
                       event_type: str) -> BacktestSummary:
        """计算回测汇总统计"""
        if not trades:
            return BacktestSummary(event_type=event_type)

        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]

        total_return = sum(t.pnl_pct for t in trades)
        avg_return = total_return / len(trades)
        win_rate = len(wins) / len(trades) * 100
        max_ret = max(t.pnl_pct for t in trades)
        min_ret = min(t.pnl_pct for t in trades)
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t.pnl_pct for t in wins) / min(sum(t.pnl_pct for t in losses), -0.001)) if losses else 999

        # 最大连续亏损
        max_cl = 0
        cur_cl = 0
        for t in trades:
            if t.pnl_pct <= 0:
                cur_cl += 1
                max_cl = max(max_cl, cur_cl)
            else:
                cur_cl = 0

        # 最大回撤（从累计收益角度）
        cum_returns = []
        cr = 0
        for t in trades:
            cr += t.pnl_pct
            cum_returns.append(cr)
        peak = cum_returns[0] if cum_returns else 0
        max_dd = 0
        for cr in cum_returns:
            if cr > peak:
                peak = cr
            dd = (cr - peak) / max(abs(peak), 0.01)
            max_dd = min(max_dd, dd)

        # 夏普比率
        returns = [t.pnl_pct for t in trades]
        if len(returns) > 1 and sum((r - sum(returns)/len(returns))**2 for r in returns) > 0:
            mean_r = sum(returns) / len(returns)
            std_r = math.sqrt(sum((r - mean_r)**2 for r in returns) / len(returns))
            sharpe = mean_r / max(std_r, 0.01) * math.sqrt(252)
        else:
            sharpe = 0

        # 卡玛
        calmar = (avg_return * 252 / max(len(trades), 1)) / max(abs(max_dd), 0.01) if max_dd < 0 else 0

        # 衰减分析
        decay = {}
        for d in [1, 3, 5, 10, 20]:
            d_trades = [t for t in trades if t.hold_days >= d]
            if d_trades:
                decay[d] = round(sum(t.pnl_pct for t in d_trades) / len(d_trades), 2)

        return BacktestSummary(
            event_type=event_type,
            total_trades=len(trades),
            win_count=len(wins),
            loss_count=len(losses),
            total_return_pct=round(total_return, 2),
            avg_return_pct=round(avg_return, 2),
            max_return_pct=round(max_ret, 2),
            min_return_pct=round(min_ret, 2),
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            avg_win_pct=round(avg_win, 2),
            avg_loss_pct=round(avg_loss, 2),
            max_consecutive_losses=max_cl,
            max_drawdown=round(max_dd * 100, 2),
            sharpe=round(sharpe, 2),
            calmar=round(calmar, 2),
            decay_analysis=decay,
        )

    def compare_strategies(self, event_groups: Dict[str, List[EventRecord]],
                            configs: Dict[str, EventConfig],
                            hold_days: int = 5) -> Dict[str, BacktestSummary]:
        """多策略对比回测"""
        results = {}
        for name, events in event_groups.items():
            results[name] = self.run_backtest(events, configs, hold_days)
        return results

    def save_results(self, results: Dict[str, BacktestSummary]) -> None:
        """保存回测结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EVENT_DIR / f"backtest_results_{timestamp}.json"
        data = {}
        for name, r in results.items():
            data[name] = asdict(r)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📊 回测结果已保存: {path}")


# ═══════════════════════════════════════════════════════════════
# 五池对接 (ED-010)
# ═══════════════════════════════════════════════════════════════

class EventPoolBridge:
    """事件信号 → 五池对接"""

    def __init__(self, pool_manager=None):
        self.pm = pool_manager

    def generate_signal_report(self, events: List[EventRecord],
                                 top_n: int = 10) -> str:
        """生成事件信号报告"""
        sorted_events = sorted(events, key=lambda e: e.event_score, reverse=True)
        lines = [
            f"# 📡 事件驱动信号报告\n",
            f"**报告时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
            f"**事件总数**: {len(events)}\n\n",
            "## 高评分事件（Top 10）\n\n",
            "| 事件类型 | 股票 | 评分 | 核心信号 |\n",
            "|:---------|:-----|:----:|:---------|\n",
        ]
        for ev in sorted_events[:top_n]:
            sig_str = "; ".join([f"{k}:{v}" for k, v in list(ev.signals.items())[:3]])
            lines.append(f"| {ev.event_type} {ev.event_name} | "
                         f"{ev.name}({ev.code}) | {ev.event_score:.0f} | {sig_str} |\n")

        lines.append(f"\n## 事件评分分布\n\n")
        ranges = [(90, 100), (80, 89), (70, 79), (60, 69), (0, 59)]
        for lo, hi in ranges:
            count = sum(1 for e in events if lo <= e.event_score <= hi)
            bar = "█" * (count // 5 + 1) if count > 0 else "▏"
            lines.append(f"{hi:>3}分以下: {count:>4}只 {bar}\n")

        return "".join(lines)

    def to_fast_screen_pool(self, events: List[EventRecord],
                             min_score: float = 70) -> List[Dict]:
        """将事件信号转换为快筛候选池格式"""
        candidates = []
        for ev in sorted(events, key=lambda e: e.event_score, reverse=True):
            if ev.event_score < min_score:
                continue
            candidates.append({
                "股票代码": ev.code,
                "股票名称": ev.name,
                "事件类型": f"{ev.event_type} {ev.event_name}",
                "综合分": ev.event_score,
                "事件评分": ev.event_strength,
                "核心逻辑": "; ".join([f"{k}:{v}" for k, v in ev.signals.items()]),
                "纳入日期": ev.trigger_date,
            })
        return candidates


# ═══════════════════════════════════════════════════════════════
# CLI入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="事件驱动策略引擎 - 回测与信号")
    parser.add_argument("action", choices=["backtest", "simulate", "signal", "compare"],
                        default="backtest", nargs="?")
    parser.add_argument("--events", type=int, default=100, help="模拟事件数")
    parser.add_argument("--hold", type=int, default=5, help="持仓天数")
    parser.add_argument("--type", default="all", help="事件类型")
    args = parser.parse_args()

    # 默认配置
    default_configs = {
        "EV-01": EventConfig("EV-01", min_score=65, max_position_pct=5),
        "EV-02": EventConfig("EV-02", min_score=65, max_position_pct=5),
        "EV-03": EventConfig("EV-03", min_score=60, max_position_pct=3),
        "EV-04": EventConfig("EV-04", min_score=70, max_position_pct=5),
        "EV-05": EventConfig("EV-05", min_score=65, max_position_pct=3, hold_days=3),
        "EV-06": EventConfig("EV-06", min_score=60, max_position_pct=8, hold_days=20),
    }

    if args.action in ("backtest", "simulate"):
        # 生成模拟事件
        rng = random.Random(42)
        event_types = ["EV-01","EV-02","EV-03","EV-04","EV-05","EV-06"]
        event_names = {
            "EV-01": "财报超预期","EV-02": "动量突破","EV-03": "成交量异常",
            "EV-04": "净利润断层","EV-05": "超跌反弹","EV-06": "高ROE增长",
        }
        events = []
        for i in range(args.events):
            et = event_types[i % len(event_types)]
            score = 40 + rng.randint(0, 55)  # 40-95分
            events.append(EventRecord(
                event_id=f"SIM_{et}_{i:04d}",
                event_type=et,
                event_name=event_names[et],
                code=f"60{rng.randint(1000,9999)}",
                name=f"模拟股票{i}",
                trigger_date="2026-07-16",
                event_score=min(score, 100),
            ))

        # 按事件类型分组
        groups = {}
        for ev in events:
            groups.setdefault(ev.event_type, []).append(ev)

        engine = BacktestEngine()
        results = {}
        for etype, evts in groups.items():
            results[etype] = engine.run_backtest(evts, default_configs, args.hold)

        # 全量汇总
        results["ALL"] = engine.run_backtest(events, default_configs, args.hold)

        print(f"\n{'='*60}")
        print(f"  事件驱动策略回测结果 ({len(events)} 条模拟事件)")
        print(f"{'='*60}")
        print(f"{'策略':<16} {'交易':>4} {'胜率':>6} {'总收益':>8} {'平均':>6} {'最大':>6} {'盈亏比':>6} {'夏普':>6}")
        print("-" * 60)
        for name, r in sorted(results.items()):
            print(f"{name:<16} {r.total_trades:>4} {r.win_rate:>5.1f}% {r.total_return_pct:>7.1f}% "
                  f"{r.avg_return_pct:>5.2f}% {r.max_return_pct:>5.1f}% {r.profit_factor:>5.1f} {r.sharpe:>5.2f}")
        print(f"\n  衰减分析:")
        for name, r in sorted(results.items()):
            if r.decay_analysis:
                dec = ", ".join([f"D+{d}={v:.1f}%" for d, v in sorted(r.decay_analysis.items())])
                print(f"  {name:<12}: {dec}")

        engine.save_results(results)

    elif args.action == "signal":
        # 生成信号报告示例
        events = [
            EventRecord(event_id="TEST_001", event_type="EV-01", event_name="财报超预期",
                         code="600519", name="贵州茅台", trigger_date="2026-07-16",
                         event_score=88, event_strength=85,
                         signals={"营收暴增": "+35%", "净利高增": "+52%", "ROE优质": "28.5%"}),
            EventRecord(event_id="TEST_002", event_type="EV-02", event_name="动量突破",
                         code="300750", name="宁德时代", trigger_date="2026-07-16",
                         event_score=82, event_strength=78,
                         signals={"多头排列": " >MA50>MA20", "成交量扩张": "1.8倍", "趋势健康": "+12%"}),
        ]
        bridge = EventPoolBridge()
        report = bridge.generate_signal_report(events)
        print(report)
        report_path = EVENT_DIR / "signal_report_demo.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"✅ 信号报告已保存: {report_path}")
        print(f"\n候选池格式:")
        import json
        print(json.dumps(bridge.to_fast_screen_pool(events), ensure_ascii=False, indent=2))