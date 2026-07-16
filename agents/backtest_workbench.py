#!/usr/bin/env python3
"""策略批量回测与对比分析工作台

BW-001~005 全功能覆盖：
- 批量回测：多策略同时回测、统一周期与基准
- 参数扫描：核心参数区间遍历、敏感性分析、最优参数筛选
- 对比分析：多策略多维度指标对比、收益风险排名、自定义权重打分
- 结果分析：回测报告批量生成、关键指标对比看板、策略优劣诊断
- 任务管理：回测任务排队、断点续跑、结果缓存、历史回测记录管理
"""
from __future__ import annotations

import json
import math
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = PROJECT_ROOT / "data" / "backtest_workbench"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR = DATA_DIR / "tasks"
RESULTS_DIR = DATA_DIR / "results"
REPORTS_DIR = DATA_DIR / "reports"
CACHE_DIR = DATA_DIR / "cache"
for d in [TASKS_DIR, RESULTS_DIR, REPORTS_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"  # 部分完成（断点续跑用）


@dataclass
class StrategyParam:
    """策略参数集"""
    name: str                          # 策略名称
    min_score: float = 75              # 最小评分
    max_position_pct: float = 10.0     # 最大仓位
    stop_loss_pct: float = -5.0        # 止损
    take_profit_pct: float = 15.0      # 止盈
    hold_days: int = 5                 # 持仓天数
    require_skeptic: bool = True       # 需要质疑
    min_score_weak: float = 85         # 弱市评分
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """单次回测结果"""
    strategy_name: str
    params: StrategyParam
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    max_return_pct: float = 0.0
    min_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    max_consecutive_losses: int = 0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    var_95: float = 0.0
    payoff_ratio: float = 0.0          # 盈亏比 = avg_win / |avg_loss|
    expectancy: float = 0.0            # 期望值 = win_rate * avg_win - (1-win_rate) * |avg_loss|
    score: float = 0.0                 # 综合打分


@dataclass
class ParamScanResult:
    """参数扫描结果"""
    param_name: str                    # 扫描的参数名
    param_values: List[Any]            # 扫描的参数值
    results: List[BacktestResult]      # 各参数对应的回测结果
    best_param: Any = None             # 最优参数
    best_result: Optional[BacktestResult] = None
    sensitivity: Dict[str, float] = field(default_factory=dict)  # 敏感性


@dataclass
class BatchTask:
    """批量回测任务"""
    task_id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    strategies: List[StrategyParam] = field(default_factory=list)
    results: List[BacktestResult] = field(default_factory=list)
    param_scans: List[ParamScanResult] = field(default_factory=list)
    total_items: int = 0
    completed_items: int = 0
    created_at: str = ""
    completed_at: str = ""
    checkpoint: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ═══════════════════════════════════════════════════════════════
# 回测模拟器（对现有回测引擎的模拟封装）
# ═══════════════════════════════════════════════════════════════

class BacktestSimulator:
    """回测模拟器 — 基于参数生成模拟回测结果

    实际使用时可替换为对接 backtest_sandbox.py 或 event_backtest.py
    """

    @staticmethod
    def run(params: StrategyParam, seed: int = None) -> BacktestResult:
        """运行单次回测，返回结果

        Args:
            params: 策略参数
            seed: 随机种子（可复现）

        Returns:
            回测结果
        """
        rng = random.Random(seed or hash(str(params)) & 0xFFFFFFFF)
        n_trades = 30 + rng.randint(0, 40)

        # 基于参数生成合理的回测统计
        base_win_rate = 45 + (params.min_score - 60) * 0.5
        base_win_rate = max(35, min(65, base_win_rate))

        wins = []
        losses = []
        for _ in range(n_trades):
            if rng.random() < base_win_rate / 100:
                win_pct = 2 + rng.random() * 8
                wins.append(win_pct)
            else:
                loss_pct = -(2 + rng.random() * 6)
                losses.append(loss_pct)

        # 止损限制
        wins = [min(w, abs(params.take_profit_pct)) for w in wins]
        losses = [max(l, params.stop_loss_pct) for l in losses]

        total_return = sum(wins) + sum(losses)
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0

        # 下行波动计算
        all_returns = wins + losses
        avg_r = sum(all_returns) / len(all_returns) if all_returns else 0
        neg_returns = [r for r in all_returns if r < 0]
        down_vol = math.sqrt(sum((r - avg_r)**2 for r in neg_returns) / max(len(neg_returns), 1)) if neg_returns else 0.01
        vol = math.sqrt(sum((r - avg_r)**2 for r in all_returns) / len(all_returns)) if all_returns else 0.01

        sharpe = (avg_r / max(vol, 0.01)) * math.sqrt(252 / max(n_trades, 1)) if vol > 0 else 0
        sortino = (avg_r / max(down_vol, 0.01)) * math.sqrt(252 / max(n_trades, 1)) if down_vol > 0 else 0

        # 最大回撤
        max_dd = 0
        peak = 0
        cum = 0
        for r in all_returns:
            cum += r
            if cum > peak:
                peak = cum
            dd = (cum - peak) / max(abs(peak), 0.01)
            max_dd = min(max_dd, dd)

        # 最大连续亏损
        max_cl = 0
        cur_cl = 0
        for r in all_returns:
            if r < 0:
                cur_cl += 1
                max_cl = max(max_cl, cur_cl)
            else:
                cur_cl = 0

        profit_factor = abs(sum(wins) / max(abs(sum(losses)), 0.01)) if losses else 999
        payoff = avg_win / max(abs(avg_loss), 0.01) if avg_loss != 0 else 999
        expectancy = win_rate / 100 * avg_win - (1 - win_rate / 100) * abs(avg_loss)

        return BacktestResult(
            strategy_name=params.name,
            params=params,
            total_trades=n_trades,
            win_count=len(wins),
            loss_count=len(losses),
            total_return_pct=round(total_return, 2),
            avg_return_pct=round(avg_r, 2) if all_returns else 0,
            max_return_pct=round(max(wins), 2) if wins else 0,
            min_return_pct=round(min(losses), 2) if losses else 0,
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            avg_win_pct=round(avg_win, 2),
            avg_loss_pct=round(avg_loss, 2),
            max_consecutive_losses=max_cl,
            max_drawdown=round(max_dd * 100, 2),
            sharpe=round(sharpe, 3),
            sortino=round(sortino, 3),
            calmar=round(total_return / max(abs(max_dd), 0.01), 2) if max_dd < 0 else 0,
            var_95=round(avg_r - 1.645 * vol, 2),
            payoff_ratio=round(payoff, 2),
            expectancy=round(expectancy, 2),
        )

    @staticmethod
    def run_batch(params_list: List[StrategyParam],
                   progress_cb: Callable = None) -> List[BacktestResult]:
        """批量运行回测"""
        results = []
        for i, p in enumerate(params_list):
            r = BacktestSimulator.run(p)
            results.append(r)
            if progress_cb:
                progress_cb(i + 1, len(params_list))
        return results


# ═══════════════════════════════════════════════════════════════
# 参数扫描 (BW-002)
# ═══════════════════════════════════════════════════════════════

class ParameterScanner:
    """参数扫描器 — 网格遍历 + 敏感性分析"""

    SCANNABLE_PARAMS = {
        "min_score": {"range": (60, 90), "step": 5, "label": "最小评分"},
        "stop_loss_pct": {"range": (-10, -3), "step": 1, "label": "止损%"},
        "take_profit_pct": {"range": (8, 25), "step": 3, "label": "止盈%"},
        "hold_days": {"range": (3, 15), "step": 2, "label": "持仓天数"},
        "max_position_pct": {"range": (3, 15), "step": 2, "label": "最大仓位%"},
        "min_score_weak": {"range": (75, 95), "step": 5, "label": "弱市评分"},
    }

    @staticmethod
    def generate_values(param_name: str) -> List[Any]:
        """生成参数扫描值列表"""
        spec = ParameterScanner.SCANNABLE_PARAMS.get(param_name)
        if not spec:
            return []
        lo, hi = spec["range"]
        step = spec["step"]
        if isinstance(lo, int) and isinstance(hi, int):
            return list(range(lo, hi + 1, step))
        vals = []
        v = lo
        while v <= hi + 0.001:
            vals.append(round(v, 1))
            v += step
        return vals

    @staticmethod
    def scan(base_params: StrategyParam, param_name: str,
              values: List[Any] = None) -> ParamScanResult:
        """对单个参数进行扫描

        Args:
            base_params: 基础策略参数
            param_name: 要扫描的参数名
            values: 参数值列表（None则使用默认区间）

        Returns:
            扫描结果（含最优参数和敏感性）
        """
        if values is None:
            values = ParameterScanner.generate_values(param_name)

        results = []
        for v in values:
            params = StrategyParam(**{k: v for k, v in asdict(base_params).items() if k != 'extra'})
            setattr(params, param_name, v)
            params.extra = base_params.extra
            r = BacktestSimulator.run(params)
            results.append(r)

        # 按综合分找最优
        scored = [(ParameterScanner._calc_composite(r), r) for r in results]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_result = scored[0][1] if scored else None

        # 敏感性分析：参数变动对各指标的影响
        sensitivity = {}
        if len(results) >= 2:
            best_composite = scored[0][0] if scored else 0
            worst_composite = scored[-1][0] if scored else 0
            sensitivity["range_composite"] = round(best_composite - worst_composite, 2)
            sensitivity["best_value"] = values[results.index(best_result)] if best_result else values[0]
            sensitivity["param_name"] = param_name

        return ParamScanResult(
            param_name=param_name,
            param_values=values,
            results=results,
            best_param=getattr(best_result.params, param_name, None) if best_result else None,
            best_result=best_result,
            sensitivity=sensitivity,
        )

    @staticmethod
    def multi_scan(base_params: StrategyParam,
                    param_names: List[str]) -> Dict[str, ParamScanResult]:
        """多参数批量扫描"""
        return {n: ParameterScanner.scan(base_params, n) for n in param_names}

    @staticmethod
    def _calc_composite(r: BacktestResult) -> float:
        """计算综合分"""
        sharpe_norm = max(0, min(r.sharpe / 3, 1)) * 30
        win_norm = max(0, min(r.win_rate / 60, 1)) * 20
        dd_norm = max(0, 1 - abs(r.max_drawdown) / 30) * 15
        pf_norm = max(0, min(r.profit_factor / 3, 1)) * 15
        payoff_norm = max(0, min(r.payoff_ratio / 3, 1)) * 10
        ret_norm = max(0, min(r.total_return_pct / 30, 1)) * 10
        return round(sharpe_norm + win_norm + dd_norm + pf_norm + payoff_norm + ret_norm, 1)


# ═══════════════════════════════════════════════════════════════
# 对比分析 (BW-003)
# ═══════════════════════════════════════════════════════════════

class CompareAnalyzer:
    """对比分析器 — 多维度对比、排名、自定义权重打分"""

    # 指标元数据
    METRICS = {
        "total_return_pct": {"name": "总收益%", "higher": True, "weight": 15},
        "win_rate": {"name": "胜率%", "higher": True, "weight": 15},
        "sharpe": {"name": "夏普", "higher": True, "weight": 20},
        "sortino": {"name": "索提诺", "higher": True, "weight": 10},
        "profit_factor": {"name": "盈亏比", "higher": True, "weight": 10},
        "payoff_ratio": {"name": "赔率", "higher": True, "weight": 5},
        "max_drawdown": {"name": "最大回撤%", "higher": False, "weight": 15},
        "max_consecutive_losses": {"name": "最大连亏", "higher": False, "weight": 5},
        "calmar": {"name": "卡玛", "higher": True, "weight": 5},
    }

    @staticmethod
    def rank(results: List[BacktestResult],
              weights: Dict[str, float] = None) -> List[Tuple[int, BacktestResult, float]]:
        """多策略排名

        Args:
            results: 回测结果列表
            weights: 自定义权重，如 {"sharpe": 30, "win_rate": 20}

        Returns:
            [(排名, 结果, 综合分), ...]
        """
        if not results:
            return []

        w = weights or {k: v["weight"] for k, v in CompareAnalyzer.METRICS.items()}

        scores = []
        for r in results:
            score = 0.0
            for metric, weight in w.items():
                values = [getattr(rr, metric, 0) for rr in results]
                if not values:
                    continue
                v = getattr(r, metric, 0)
                mn, mx = min(values), max(values)
                if mx > mn:
                    norm = (v - mn) / (mx - mn)
                else:
                    norm = 0.5
                meta = CompareAnalyzer.METRICS.get(metric, {})
                if not meta.get("higher", True):
                    norm = 1 - norm
                score += norm * weight
            scores.append((r, round(score, 1)))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [(i + 1, r, s) for i, (r, s) in enumerate(scores)]

    @staticmethod
    def diagnosis(r: BacktestResult) -> List[str]:
        """策略诊断 — 输出优缺点"""
        issues = []

        if r.win_rate < 40:
            issues.append(f"🔴 胜率偏低({r.win_rate:.1f}%)")
        elif r.win_rate > 55:
            issues.append(f"🟢 胜率优秀({r.win_rate:.1f}%)")

        if r.sharpe < 0.5:
            issues.append(f"🔴 夏普偏低({r.sharpe:.2f})")
        elif r.sharpe > 1.5:
            issues.append(f"🟢 夏普优秀({r.sharpe:.2f})")

        if r.max_drawdown < -20:
            issues.append(f"🔴 回撤过大({r.max_drawdown:.1f}%)")
        elif r.max_drawdown > -10:
            issues.append(f"🟢 回撤可控({r.max_drawdown:.1f}%)")

        if r.profit_factor < 1.2:
            issues.append(f"🔴 盈亏比偏低({r.profit_factor:.2f})")
        elif r.profit_factor > 2.5:
            issues.append(f"🟢 盈亏比优秀({r.profit_factor:.2f})")

        if r.max_consecutive_losses > 5:
            issues.append(f"🔴 连亏次数多({r.max_consecutive_losses}次)")
        if r.total_trades < 20:
            issues.append(f"🟡 样本量不足({r.total_trades}笔)")
        elif r.total_trades > 50:
            issues.append(f"🟢 样本充足({r.total_trades}笔)")

        return issues if issues else ["✅ 策略整体健康"]

    @staticmethod
    def compare_table(results: List[BacktestResult]) -> str:
        """生成对比表格"""
        lines = [
            f"\n{'='*80}",
            f"  多策略对比分析",
            f"{'='*80}",
            f"{'排名':>4} {'策略名':<16} {'总收益%':>8} {'胜率%':>6} {'夏普':>6} "
            f"{'索提诺':>7} {'回撤%':>7} {'盈亏比':>6} {'连亏':>4} {'综合分':>6}",
            "-" * 80,
        ]
        ranked = CompareAnalyzer.rank(results)
        for rank, r, score in ranked:
            lines.append(
                f"{rank:>4} {r.strategy_name:<16} {r.total_return_pct:>7.1f}% "
                f"{r.win_rate:>5.1f}% {r.sharpe:>5.2f} {r.sortino:>6.2f} "
                f"{r.max_drawdown:>6.1f}% {r.profit_factor:>5.1f} "
                f"{r.max_consecutive_losses:>3} {score:>5.1f}")
        return "\n".join(lines)

    @staticmethod
    def param_sensitivity_table(scan: ParamScanResult) -> str:
        """参数敏感性分析表格"""
        lines = [
            f"\n参数扫描: {scan.param_name}",
            f"{'参数值':>8} {'总收益%':>8} {'胜率%':>6} {'夏普':>6} {'回撤%':>7} {'盈亏比':>6} {'综合分':>6}",
            "-" * 50,
        ]
        for i, v in enumerate(scan.param_values):
            r = scan.results[i] if i < len(scan.results) else None
            if r:
                s = ParameterScanner._calc_composite(r)
                lines.append(f"{str(v):>8} {r.total_return_pct:>7.1f}% {r.win_rate:>5.1f}% "
                              f"{r.sharpe:>5.2f} {r.max_drawdown:>6.1f}% {r.profit_factor:>5.1f} {s:>5.1f}")
        if scan.best_param is not None:
            lines.append(f"\n✅ 最优参数: {scan.param_name}={scan.best_param}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 报告生成 (BW-004)
# ═══════════════════════════════════════════════════════════════

class ReportGenerator:
    """报告生成器"""

    @staticmethod
    def generate_ranked_report(results: List[BacktestResult],
                                 title: str = "批量回测对比报告") -> str:
        """生成排名报告"""
        ranked = CompareAnalyzer.rank(results)
        lines = [
            f"# {title}",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**策略数量**: {len(results)}",
            "",
            "## 综合排名",
            "",
            "| 排名 | 策略 | 总收益% | 胜率% | 夏普 | 索提诺 | 回撤% | 盈亏比 | 连亏 | 综合分 |",
            "|:----:|:-----|:------:|:----:|:----:|:------:|:-----:|:-----:|:---:|:-----:|",
        ]
        for rank, r, score in ranked:
            lines.append(
                f"| {rank} | {r.strategy_name} | {r.total_return_pct:.1f}% | "
                f"{r.win_rate:.1f}% | {r.sharpe:.2f} | {r.sortino:.2f} | "
                f"{r.max_drawdown:.1f}% | {r.profit_factor:.1f} | "
                f"{r.max_consecutive_losses} | {score:.1f} |")

        # 最佳策略详情
        if ranked:
            _, best, _ = ranked[0]
            lines.extend(["", "## 🏆 最佳策略", ""])
            lines.extend([f"- {k}: {getattr(best, k)}" for k in
                          ["strategy_name", "total_trades", "win_rate",
                           "sharpe", "sortino", "max_drawdown", "profit_factor"]])

            # 诊断
            lines.extend(["", "## 策略诊断", ""])
            for rank, r, score in ranked:
                lines.append(f"### {rank}. {r.strategy_name}")
                for d in CompareAnalyzer.diagnosis(r):
                    lines.append(f"- {d}")

        # 评分分布
        if ranked:
            scores = [s for _, _, s in ranked]
            if scores:
                avg = sum(scores) / len(scores)
                lines.extend(["", "## 评分分布", ""])
                for rank, r, score in ranked:
                    bar = "█" * max(1, int(score / 5))
                    lines.append(f"  {rank}. {r.strategy_name:<20} {score:>5.1f} {bar}")

        return "\n".join(lines)

    @staticmethod
    def generate_param_report(scan: ParamScanResult) -> str:
        """生成参数扫描报告"""
        lines = [
            f"# 参数扫描报告: {scan.param_name}",
            f"**最优参数**: {scan.param_name}={scan.best_param}",
            "",
            "| 参数值 | 总收益% | 胜率% | 夏普 | 回撤% | 盈亏比 | 综合分 |",
            "|:-----:|:------:|:----:|:----:|:-----:|:-----:|:-----:|",
        ]
        for i, v in enumerate(scan.param_values):
            r = scan.results[i] if i < len(scan.results) else None
            if r:
                s = ParameterScanner._calc_composite(r)
                lines.append(f"| {v} | {r.total_return_pct:.1f}% | {r.win_rate:.1f}% | "
                              f"{r.sharpe:.2f} | {r.max_drawdown:.1f}% | "
                              f"{r.profit_factor:.1f} | {s:.1f} |")

        # 敏感性
        if scan.sensitivity:
            lines.extend(["", "## 敏感性分析", ""])
            lines.append(f"- 综合分波动范围: {scan.sensitivity.get('range_composite', 0)}")
            lines.append(f"- 最优参数值: {scan.sensitivity.get('best_value', 'N/A')}")
            lines.append(f"- 参数名: {scan.sensitivity.get('param_name', 'N/A')}")

        return "\n".join(lines)

    @staticmethod
    def save_report(content: str, filename: str) -> Path:
        """保存报告"""
        path = REPORTS_DIR / filename
        path.write_text(content, encoding="utf-8")
        return path


# ═══════════════════════════════════════════════════════════════
# 任务管理 (BW-005)
# ═══════════════════════════════════════════════════════════════

class TaskManager:
    """任务管理器 — 排队/断点续跑/结果缓存"""

    def __init__(self):
        self._tasks: Dict[str, BatchTask] = {}
        # 确保目录存在（支持clean后重建）
        for d in [TASKS_DIR, RESULTS_DIR, REPORTS_DIR, CACHE_DIR]:
            d.mkdir(parents=True, exist_ok=True)
        self._load()

    def create_task(self, name: str,
                     strategies: List[StrategyParam]) -> str:
        """创建批量回测任务"""
        tid = f"BT{int(time.time())%100000:05d}"
        task = BatchTask(
            task_id=tid, name=name,
            strategies=strategies,
            total_items=len(strategies),
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._tasks[tid] = task
        self._save()
        return tid

    def run_task(self, task_id: str,
                  callback: Callable = None) -> BatchTask:
        """执行批量回测任务（支持断点续跑）

        断点续跑逻辑：
        1. 检查task.checkpoint中的已完成条目
        2. 跳过已完成的，从断点继续
        3. 每完成一条更新checkpoint
        """
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        task.status = TaskStatus.RUNNING
        start_idx = task.checkpoint.get("completed_index", 0)

        for i in range(start_idx, task.total_items):
            params = task.strategies[i]
            try:
                result = BacktestSimulator.run(params)
                # 保存结果
                result_path = RESULTS_DIR / f"{task_id}_{i:04d}.json"
                result_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2),
                                        encoding="utf-8")
                task.results.append(result)
                task.completed_items = i + 1

                # 更新checkpoint（核心断点逻辑）
                task.checkpoint = {
                    "completed_index": i + 1,
                    "last_strategy": params.name,
                    "last_time": datetime.now().strftime("%H:%M:%S"),
                }
                self._save()

                if callback:
                    callback(i + 1, task.total_items, params.name)

            except Exception as e:
                task.error = f"第{i}条失败({params.name}): {e}"
                task.status = TaskStatus.FAILED
                self._save()
                return task

        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return task

    def run_param_scan(self, task_id: str, base_params: StrategyParam,
                        param_names: List[str]) -> BatchTask:
        """执行参数扫描任务"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        task.status = TaskStatus.RUNNING
        for pname in param_names:
            scan = ParameterScanner.scan(base_params, pname)
            task.param_scans.append(scan)
            task.checkpoint["last_scan"] = pname
            self._save()

        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return task

    def get_task(self, task_id: str) -> Optional[BatchTask]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> List[BatchTask]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at or "", reverse=True)

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status == TaskStatus.COMPLETED:
            return False
        task.status = TaskStatus.CANCELLED
        self._save()
        return True

    def get_cached_result(self, task_id: str, index: int) -> Optional[BacktestResult]:
        path = RESULTS_DIR / f"{task_id}_{index:04d}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["params"] = StrategyParam(**data.get("params", {}))
                return BacktestResult(**data)
            except Exception:
                pass
        return None

    def _save(self):
        path = TASKS_DIR / "tasks.json"
        data = {}
        for tid, task in self._tasks.items():
            d = asdict(task)
            data[tid] = d
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self):
        path = TASKS_DIR / "tasks.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for tid, d in data.items():
                    d["strategies"] = [StrategyParam(**s) for s in d.get("strategies", [])]
                    d["results"] = [BacktestResult(**r) for r in d.get("results", [])]
                    d["param_scans"] = [ParamScanResult(**ps) for ps in d.get("param_scans", [])]
                    d["status"] = TaskStatus(d["status"])
                    self._tasks[tid] = BatchTask(**d)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# 工作台入口 (BW-001)
# ═══════════════════════════════════════════════════════════════

class BatchWorkbench:
    """策略批量回测与对比分析工作台"""

    def __init__(self):
        self.tasks = TaskManager()

    def run_batch(self, strategies: List[StrategyParam],
                   name: str = None) -> BatchTask:
        """运行批 量回测"""
        n = name or f"批量回测_{datetime.now().strftime('%m%d_%H%M')}"
        tid = self.tasks.create_task(n, strategies)
        return self.tasks.run_task(tid)

    def run_param_scan(self, base_params: StrategyParam,
                        param_names: List[str],
                        name: str = None) -> BatchTask:
        """运行参数扫描"""
        n = name or f"参数扫描_{'_'.join(param_names)}"
        tid = self.tasks.create_task(n, [base_params])
        return self.tasks.run_param_scan(tid, base_params, param_names)

    def generate_reports(self, task_id: str) -> List[Path]:
        """为任务生成所有报告"""
        task = self.tasks.get_task(task_id)
        if not task or not task.results:
            return []

        paths = []
        # 排名报告
        content = ReportGenerator.generate_ranked_report(task.results)
        p = ReportGenerator.save_report(content, f"rank_{task_id}.md")
        paths.append(p)

        # 参数扫描报告
        for scan in task.param_scans:
            content = ReportGenerator.generate_param_report(scan)
            p = ReportGenerator.save_report(content, f"param_{task_id}_{scan.param_name}.md")
            paths.append(p)

        return paths


# ═══════════════════════════════════════════════════════════════
# CLI入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="批量回测与对比分析工作台")
    sub = parser.add_subparsers(dest="action")

    # batch
    p_batch = sub.add_parser("batch", help="运行批量回测")
    p_batch.add_argument("--count", type=int, default=5, help="生成策略数")
    p_batch.add_argument("--name", default="")

    # paramscan
    p_scan = sub.add_parser("paramscan", help="参数扫描")
    p_scan.add_argument("--param", nargs="+", default=["min_score", "stop_loss_pct"])

    # list
    sub.add_parser("list", help="列出所有任务")

    # report
    p_rep = sub.add_parser("report", help="生成报告")
    p_rep.add_argument("--id", required=True)

    # status
    p_st = sub.add_parser("status", help="查看任务状态")
    p_st.add_argument("--id")

    # compare
    p_cmp = sub.add_parser("compare", help="对比已有结果")
    p_cmp.add_argument("--id", required=True)

    # resume (demo断点续跑)
    p_resume = sub.add_parser("resume", help="断点续跑演示")
    p_resume.add_argument("--fail-at", type=int, default=3)

    args = parser.parse_args()
    wb = BatchWorkbench()

    if args.action == "batch":
        # 生成随机策略批量回测
        rng = random.Random(42)
        strategies = []
        for i in range(args.count):
            s = StrategyParam(
                name=f"策略_{chr(65+i)}",
                min_score=65 + rng.randint(0, 25),
                stop_loss_pct=round(-5 - rng.random() * 5, 1),
                take_profit_pct=round(10 + rng.random() * 15, 1),
                hold_days=rng.randint(3, 10),
            )
            strategies.append(s)
        task = wb.run_batch(strategies, args.name)
        print(f"✅ 批量回测完成: {task.task_id}")
        print(CompareAnalyzer.compare_table(task.results))
        wb.generate_reports(task.task_id)

    elif args.action == "paramscan":
        base = StrategyParam(name="基准策略", min_score=75, stop_loss_pct=-5,
                              take_profit_pct=15, hold_days=5)
        task = wb.run_param_scan(base, args.param)
        print(f"✅ 参数扫描完成: {task.task_id}")
        for scan in task.param_scans:
            print(CompareAnalyzer.param_sensitivity_table(scan))
        wb.generate_reports(task.task_id)

    elif args.action == "list":
        tasks = wb.tasks.list_tasks()
        print(f"{'ID':<10} {'名称':<20} {'状态':<12} {'完成/总数':<12} {'创建时间':<20}")
        print("-" * 75)
        for t in tasks:
            print(f"{t.task_id:<10} {t.name:<20} {t.status.value:<12} "
                  f"{t.completed_items}/{t.total_items:<8} {t.created_at:<20}")

    elif args.action == "status":
        if args.id:
            t = wb.tasks.get_task(args.id)
            if t:
                print(f"=== {t.task_id} {t.name} ===")
                print(f"  状态: {t.status.value}")
                print(f"  进度: {t.completed_items}/{t.total_items}")
                print(f"  结果数: {len(t.results)}")
                print(f"  参数扫描: {len(t.param_scans)}")
                if t.results:
                    ranked = CompareAnalyzer.rank(t.results)
                    print(f"  最佳: {ranked[0][1].strategy_name} 综合分{ranked[0][2]}")
            else:
                print(f"❌ 未找到: {args.id}")
        else:
            tasks = wb.tasks.list_tasks()
            print(f"任务总数: {len(tasks)}")
            print(f"已完成: {sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)}")
            print(f"运行中: {sum(1 for t in tasks if t.status == TaskStatus.RUNNING)}")

    elif args.action == "report":
        paths = wb.generate_reports(args.id)
        for p in paths:
            print(f"✅ 报告已生成: {p}")

    elif args.action == "compare":
        task = wb.tasks.get_task(args.id)
        if task and task.results:
            print(CompareAnalyzer.compare_table(task.results))
            print("\n=== 策略诊断 ===")
            for rank, r, score in CompareAnalyzer.rank(task.results)[:3]:
                print(f"\n{rank}. {r.strategy_name} (综合分{score})")
                for d in CompareAnalyzer.diagnosis(r):
                    print(f"  {d}")
        else:
            print(f"❌ 任务无结果: {args.id}")

    elif args.action == "resume":
        # 断点续跑演示
        print(f"📋 断点续跑演示:")
        print(f"  计划执行5个策略，在第{args.fail_at}个处模拟失败")
        strategies = [StrategyParam(name=f"策略_{chr(65+i)}") for i in range(5)]

        # 第一次：模拟失败
        task_id = wb.tasks.create_task("断点续跑演示", strategies)
        task = wb.tasks.get_task(task_id)
        task.status = TaskStatus.RUNNING
        for i in range(args.fail_at):
            result = BacktestSimulator.run(strategies[i], seed=i)
            task.results.append(result)
            task.completed_items = i + 1
            task.checkpoint = {"completed_index": i + 1, "last_strategy": strategies[i].name}
            wb.tasks._save()

        # 标记失败
        task.status = TaskStatus.FAILED
        task.error = f"模拟失败于第{args.fail_at}个"
        wb.tasks._save()
        print(f"  ❌ 执行到第{args.fail_at}个失败")

        # 恢复：从checkpoint续跑
        print(f"  🔄 从checkpoint恢复: 已完成{task.checkpoint.get('completed_index', 0)}个")
        recovered = wb.tasks.run_task(task_id)
        print(f"  ✅ 续跑完成: {recovered.completed_items}/{recovered.total_items}")
        print(CompareAnalyzer.compare_table(recovered.results))

    else:
        parser.print_help()