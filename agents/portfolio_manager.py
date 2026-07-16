#!/usr/bin/env python3
"""多策略组合管理模块 v2.0

四大核心功能：
1. 策略池管理 — 注册/启停/配置/版本/状态监控
2. 资金分配 — 固定比例/风险平价/凯利/绩效动态/多层级分配
3. 组合风控 — 相关性监控/熔断/总仓位约束/容量/跨策略暴露
4. 动态再平衡 — 定期/偏离度/平滑/优胜劣汰
5. 组合绩效 — 收益/风险/归因/贡献度拆解

与PoolManager(只读)、backtest_sandbox无缝对接，零侵入现有单策略逻辑。
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

# ── 配置加载 ─────────────────────────────────────

_PORTFOLIO_CONFIG = None  # 全局缓存

def _load_portfolio_config() -> dict:
    """从 config.yaml 加载组合管理配置，回退默认值"""
    global _PORTFOLIO_CONFIG
    if _PORTFOLIO_CONFIG is not None:
        return _PORTFOLIO_CONFIG

    cfg = {
        "enabled": True,
        "default_allocation_method": "risk_parity",
        "rebalance": {
            "periodic": True,
            "periodic_interval": "weekly",
            "periodic_day": "Monday",
            "deviation_threshold": 0.05,
            "max_turnover_per_trade": 0.20,
            "cooldown_hours": 1,
        },
        "risk": {
            "max_total_positions": 30,
            "max_total_position_pct": 100.0,
            "correlation_threshold_warning": 0.70,
            "correlation_threshold_force": 0.85,
            "cross_strategy_position_limit": 30.0,
            "circuit_breaker_recovery": 0.50,
        },
        "performance": {
            "risk_free_rate": 2.5,
            "evaluation_period_days": 30,
            "survival_competition_days": 30,
            "survival_elimination_count": 1,
        },
        "data_dir": "data/portfolio",
    }
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                full = yaml.safe_load(f)
            pc = full.get("portfolio", {})
            if pc:
                cfg.update(pc)
                # 递归合并子节
                for section in ("rebalance", "risk", "performance"):
                    if section in pc:
                        cfg[section].update(pc[section])
        except Exception:
            pass  # 文件不存在或格式错误，使用默认值
    _PORTFOLIO_CONFIG = cfg
    return cfg


# ── 数据模型 ─────────────────────────────────────

@dataclass
class StrategyConfig:
    """单策略配置（v2，增强版）"""
    name: str                              # 策略名称
    enabled: bool = True                   # 是否启用
    description: str = ""                  # 策略说明
    allocation: float = 0.0                # 当前分配资金比例(0-1)
    min_allocation: float = 0.05           # 最低分配比例
    max_allocation: float = 0.40           # 最高分配比例
    max_drawdown: float = -15.0            # 最大回撤容忍(%)
    max_position_pct: float = 10.0         # 单票最大仓位(%)
    max_positions: int = 5                 # 最大持仓数量
    stop_loss_pct: float = -5.0            # 单票止损(%)
    max_daily_pnl_pct: float = -3.0        # 单日最大亏损(%)
    rebalance_threshold: float = 0.05      # 偏离度再平衡触发(%)
    created_at: str = ""                   # 创建时间
    version: str = "1.0"                   # 版本
    tags: List[str] = field(default_factory=list)  # 标签
    metrics: Dict[str, float] = field(default_factory=dict)  # 运行时指标
    versions: List[Dict] = field(default_factory=list)  # 版本历史（最多5条）
    status: str = "active"                 # active / paused / drawdown_warning / circuit_triggered / stopped

    @property
    def derived_status(self) -> str:
        """基于metrics推导运行状态"""
        if not self.enabled:
            return "paused"
        dd = self.metrics.get("drawdown", 0)
        if dd <= self.max_drawdown:
            return "circuit_triggered"
        if dd <= self.max_drawdown * 0.7:
            return "drawdown_warning"
        return "active"


@dataclass
class PortfolioState:
    """组合状态快照（v2，增强版）"""
    timestamp: str = ""
    total_capital: float = 1000000         # 总资金
    used_capital: float = 0.0              # 已用资金
    free_capital: float = 0.0              # 可用资金
    allocated: Dict[str, float] = field(default_factory=dict)  # 策略分配额
    drawdown: float = 0.0                  # 组合回撤
    daily_pnl: float = 0.0                 # 单日盈亏
    cumulative_pnl: float = 0.0            # 累计盈亏
    sharpe_ratio: float = 0.0              # 夏普
    sortino_ratio: float = 0.0             # 索提诺
    calmar_ratio: float = 0.0              # 卡玛
    win_rate: float = 0.0                  # 胜率
    volatility: float = 0.0                # 波动率
    var_95: float = 0.0                    # VaR 95%
    total_trades: int = 0
    correlation_matrix: Dict[str, Dict[str, float]] = field(default_factory=dict)
    allocation_method: str = "equal"
    last_rebalance: str = ""
    alerts: List[str] = field(default_factory=list)


# ── 核心管理器 ───────────────────────────────────

class PortfolioManager:
    """多策略组合管理器 v2.0

    覆盖五大子模块：
    - StrategyManager   : 策略池管理（版本/状态/监控）
    - AllocationEngine  : 资金分配（多种算法+多层级）
    - RiskController    : 组合风控（相关性/熔断/约束）
    - RebalanceEngine   : 动态再平衡（定期/偏离度/优胜劣汰）
    - PerformanceAnalyzer : 组合绩效（收益/风险/归因）
    """

    def __init__(self):
        self.cfg = _load_portfolio_config()
        dp = self.cfg.get("data_dir", "data/portfolio")
        self._base_dir = PROJECT_ROOT / dp
        self.STRATEGY_DIR = self._base_dir / "strategies"
        self.HISTORY_DIR = self._base_dir / "history"
        self.PERF_DIR = self._base_dir / "performance"
        self.REPORT_DIR = self._base_dir / "reports"
        self.PORTFOLIO_FILE = self._base_dir / "portfolio_state.json"
        self.CIRCUIT_FILE = self._base_dir / "circuit_state.json"
        self.CORR_CACHE_FILE = self._base_dir / "correlation_cache.json"
        self.CHECKPOINT_FILE = self._base_dir / "checkpoint.json"
        for d in [self.STRATEGY_DIR, self.HISTORY_DIR, self.PERF_DIR, self.REPORT_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        self._strategies: Dict[str, StrategyConfig] = {}
        self._state = PortfolioState()
        # 上次再平衡时间（防抖用）
        self._last_rebalance_time: Optional[datetime] = None
        # 熔断状态（进程持久化）
        self._circuit_breakers: Dict[str, bool] = {}  # {策略名: 是否熔断}

        self._load_state()
        self._load_strategies()
        self._load_circuit_state()
        self._load_correlation_cache()

    # ═══════════════════════════════════════════════════
    # PM-001：策略池管理（版本管理 + 状态监控）
    # ═══════════════════════════════════════════════════

    def register_strategy(self, config: StrategyConfig) -> bool:
        """注册新策略"""
        if config.name in self._strategies:
            return False
        config.created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        config.version = "1.0"
        # 记录初始版本快照
        config.versions = [self._version_snapshot(config)]
        self._strategies[config.name] = config
        self._save_strategies()
        return True

    def enable_strategy(self, name: str) -> bool:
        """启用策略"""
        if name not in self._strategies:
            return False
        self._strategies[name].enabled = True
        self._strategies[name].status = "active"
        self._save_strategies()
        return True

    def disable_strategy(self, name: str) -> bool:
        """停用策略"""
        if name not in self._strategies:
            return False
        self._strategies[name].enabled = False
        self._strategies[name].status = "paused"
        self._save_strategies()
        return True

    def toggle_strategy(self, name: str) -> bool:
        """切换启停"""
        if name not in self._strategies:
            return False
        s = self._strategies[name]
        if s.enabled:
            return self.disable_strategy(name)
        return self.enable_strategy(name)

    def update_strategy(self, name: str, **kwargs) -> bool:
        """更新策略参数（自动版本管理）"""
        if name not in self._strategies:
            return False
        s = self._strategies[name]
        changed = False
        for k, v in kwargs.items():
            if hasattr(s, k) and getattr(s, k) != v:
                setattr(s, k, v)
                changed = True
        if changed:
            # ══ PM-001: 自动版本管理 ══
            self._auto_version(s)
        self._save_strategies()
        return True

    def _auto_version(self, s: StrategyConfig) -> None:
        """自动递增版本号并保存快照"""
        parts = s.version.split(".")
        try:
            major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            s.version = f"{major}.{minor + 1}"
        except (ValueError, IndexError):
            s.version = "1.1"
        snap = self._version_snapshot(s)
        s.versions.append(snap)
        # 只保留最近5个版本
        if len(s.versions) > 5:
            s.versions = s.versions[-5:]

    def _version_snapshot(self, s: StrategyConfig) -> dict:
        """生成版本快照"""
        core = {k: getattr(s, k) for k in [
            "name", "allocation", "min_allocation", "max_allocation",
            "max_drawdown", "max_position_pct", "max_positions",
            "stop_loss_pct", "rebalance_threshold", "description"
        ]}
        core["version"] = s.version
        core["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return core

    def get_version_history(self, name: str) -> List[Dict]:
        """获取版本历史"""
        if name not in self._strategies:
            return []
        return self._strategies[name].versions

    def rollback_version(self, name: str, version: str) -> bool:
        """回滚到指定版本"""
        s = self._strategies.get(name)
        if not s:
            return False
        for v in s.versions:
            if v.get("version") == version:
                # 恢复核心参数
                for k in ["allocation", "min_allocation", "max_allocation",
                          "max_drawdown", "max_position_pct", "max_positions",
                          "stop_loss_pct", "rebalance_threshold", "description"]:
                    if k in v:
                        setattr(s, k, v[k])
                s.version = version
                self._save_strategies()
                return True
        return False

    def get_strategy_status(self, name: str) -> Dict[str, Any]:
        """获取策略详细运行状态"""
        s = self._strategies.get(name)
        if not s:
            return {}
        dd = s.metrics.get("drawdown", 0)
        return {
            "name": s.name,
            "enabled": s.enabled,
            "status": s.derived_status,
            "allocation": s.allocation,
            "version": s.version,
            "running_days": s.metrics.get("running_days", 0),
            "total_return": s.metrics.get("total_return", 0),
            "drawdown": dd,
            "drawdown_ratio": round(dd / max(abs(s.max_drawdown), 0.01), 2) if s.max_drawdown < 0 else 0,
            "sharpe": s.metrics.get("sharpe", 0),
            "win_rate": s.metrics.get("win_rate", 0),
            "trades_count": s.metrics.get("trades_count", 0),
            "max_consecutive_losses": s.metrics.get("max_consecutive_losses", 0),
            "volatility": s.metrics.get("volatility", 0),
            "circuit_triggered": self._circuit_breakers.get(name, False),
        }

    def list_strategies(self) -> List[StrategyConfig]:
        """列出所有策略"""
        return list(self._strategies.values())

    def get_enabled_strategies(self) -> List[StrategyConfig]:
        """获取启用策略列表"""
        return [s for s in self._strategies.values() if s.enabled]

    def remove_strategy(self, name: str) -> bool:
        """移除策略"""
        if name not in self._strategies:
            return False
        del self._strategies[name]
        self._circuit_breakers.pop(name, None)
        self._save_strategies()
        self._save_circuit_state()
        return True

    # ═══════════════════════════════════════════════════
    # PM-002：资金分配（多层级分配）
    # ═══════════════════════════════════════════════════

    def allocate_fixed(self, ratios: Dict[str, float]) -> Dict[str, float]:
        """固定比例分配"""
        total = sum(ratios.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in ratios.items()}

    def allocate_risk_parity(self, volatilities: Dict[str, float]) -> Dict[str, float]:
        """风险平价分配：波动率越低分配越多"""
        if not volatilities:
            return {}
        inv_vol = {k: 1.0 / max(v, 0.01) for k, v in volatilities.items()}
        total = sum(inv_vol.values())
        return {k: v / total for k, v in inv_vol.items()}

    def allocate_kelly(self, win_rates: Dict[str, float],
                       avg_wins: Dict[str, float],
                       avg_losses: Dict[str, float]) -> Dict[str, float]:
        """凯利公式分配：f* = (p * b - q) / b"""
        ratios = {}
        for k in win_rates:
            p = win_rates.get(k, 0) / 100.0
            q = 1.0 - p
            avg_win = abs(avg_wins.get(k, 0))
            avg_loss = abs(avg_losses.get(k, 0.01))
            b = avg_win / max(avg_loss, 0.01)
            f_raw = (p * b - q) / max(b, 0.01)
            f = max(0, min(f_raw, 0.4))  # 半凯利上限40%
            ratios[k] = f
        total = sum(ratios.values()) or 1.0
        return {k: v / total for k, v in ratios.items()}

    def allocate_by_performance(self, metrics: Dict[str, Dict[str, float]],
                                method: str = "sharpe") -> Dict[str, float]:
        """按绩效动态调整"""
        scores = {}
        for name, m in metrics.items():
            if method == "sharpe":
                scores[name] = max(0, m.get("sharpe", 0))
            elif method == "win_rate":
                scores[name] = m.get("win_rate", 0) / 100.0
            elif method == "return":
                scores[name] = max(0, m.get("total_return", 0) + 10) / 100.0
            else:
                scores[name] = 0.1
        total = sum(scores.values()) or 1.0
        return {k: max(0.05, min(v / total, 0.4)) for k, v in scores.items()}

    def allocate_multi_tier(self, method: str = "equal",
                            pool_data: Optional[Dict[str, List[Dict]]] = None,
                            smooth_factor: float = 0.3,
                            **kwargs) -> Tuple[Dict[str, Dict[str, float]], Dict[str, float]]:
        """多层级分配（策略层 + 标的层，PM-002）

        Args:
            method: 策略层分配方法
            pool_data: {策略名: [标的dict列表]}, 标的需含"评分"或"综合分"
            smooth_factor: 平滑系数（0=纯旧, 1=纯新）

        Returns:
            (strategy_positions, strategy_allocations)
            strategy_positions: {策略名: {标的代码: 资金比例(总资金%)}}
            strategy_allocations: {策略名: 总资金比例}
        """
        # 策略层分配
        strategy_alloc = self._apply_allocation(method, **kwargs)
        if not strategy_alloc:
            return {}, {}

        # 平滑（避免分配突变）
        if smooth_factor < 1.0:
            for s in self.get_enabled_strategies():
                old = s.allocation
                new = strategy_alloc.get(s.name, old)
                strategy_alloc[s.name] = old * (1 - smooth_factor) + new * smooth_factor

        # 标的层分配
        positions: Dict[str, Dict[str, float]] = {}
        for s_name, s_pct in strategy_alloc.items():
            s = self._strategies.get(s_name)
            if not s or not s.enabled:
                continue
            stocks = (pool_data or {}).get(s_name, [])
            if not stocks:
                positions[s_name] = {}
                continue

            # 按评分计算权重
            scored = []
            for stk in stocks:
                score = float(stk.get("评分") or stk.get("综合分") or stk.get("综合评分", 0))
                if score > 0:
                    scored.append((stk, score))
            if not scored:
                # 无评分则等分
                equal_pct = (s_pct * s.max_position_pct / 100.0) / max(len(stocks), 1)
                pos = {str(stk.get("代码", stk.get("股票代码", f"unk_{i}"))): equal_pct
                       for i, stk in enumerate(stocks)}
                positions[s_name] = pos
                continue

            total_score = sum(score for _, score in scored)
            cap = s_pct * self._state.total_capital  # 策略分配资金
            pos = {}
            for stk, score in scored:
                code = str(stk.get("代码", stk.get("股票代码", "")))
                if not code:
                    continue
                raw_pct = (score / total_score) * s_pct * (s.max_position_pct / 100.0)
                # 不超过单票上限
                max_single_pct = s.max_position_pct / 100.0
                raw_pct = min(raw_pct, max_single_pct)
                pos[code] = round(raw_pct, 4)
            positions[s_name] = pos

        return positions, strategy_alloc

    def _apply_allocation(self, method: str, **kwargs) -> Dict[str, float]:
        """统一分配入口"""
        strategies = self.get_enabled_strategies()
        names = [s.name for s in strategies]
        if not names:
            return {}

        # 排除熔断中的策略
        active = [n for n in names if not self._circuit_breakers.get(n, False)]
        # 熔断策略分配为0
        result = {}
        circuit_names = [n for n in names if self._circuit_breakers.get(n, False)]
        for n in circuit_names:
            result[n] = 0.0
        if not active:
            return result

        if method == "equal":
            ratio = 1.0 / len(active)
            for n in active:
                result[n] = ratio
        elif method == "fixed":
            fixed = self.allocate_fixed(kwargs.get("ratios", {}))
            for n in active:
                result[n] = fixed.get(n, 0)
        elif method == "risk_parity":
            rp = self.allocate_risk_parity(kwargs.get("volatilities", {}))
            for n in active:
                result[n] = rp.get(n, 0)
        elif method == "kelly":
            kl = self.allocate_kelly(
                kwargs.get("win_rates", {}),
                kwargs.get("avg_wins", {}),
                kwargs.get("avg_losses", {}))
            for n in active:
                result[n] = kl.get(n, 0)
        elif method == "performance":
            pf = self.allocate_by_performance(kwargs.get("metrics", {}), kwargs.get("method", "sharpe"))
            for n in active:
                result[n] = pf.get(n, 0)
        else:
            # 默认等分
            ratio = 1.0 / len(active)
            for n in active:
                result[n] = ratio

        # 归一化到100%
        total = sum(result.values())
        if total > 0:
            result = {k: v / total for k, v in result.items()}
        # 校验min/max约束
        for s in strategies:
            if s.name in result:
                result[s.name] = max(s.min_allocation, min(result[s.name], s.max_allocation))
        # 再次归一化
        total = sum(result.values())
        if total > 0:
            result = {k: v / total for k, v in result.items()}
        return result

    def allocate(self, method: str = "equal", **kwargs) -> Dict[str, float]:
        """统一分配入口（兼容旧接口，仅返回策略层分配）"""
        return self._apply_allocation(method, **kwargs)

    # ═══════════════════════════════════════════════════
    # PM-003：相关性监控
    # ═══════════════════════════════════════════════════

    def _load_correlation_cache(self) -> None:
        """加载相关性缓存"""
        if self.CORR_CACHE_FILE.exists():
            try:
                data = json.loads(self.CORR_CACHE_FILE.read_text(encoding="utf-8"))
                if data:
                    self._state.correlation_matrix = data.get("matrix", {})
                    self._state.alerts = data.get("alerts", [])
            except Exception:
                pass

    def _save_correlation_cache(self) -> None:
        """保存相关性缓存"""
        try:
            data = {
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "window_days": self.cfg["performance"]["evaluation_period_days"],
                "matrix": self._state.correlation_matrix,
                "alerts": self._state.alerts if hasattr(self._state, "alerts") else [],
            }
            self.CORR_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def update_correlation(self, perf_data: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
        """更新策略间相关系数矩阵（PM-003）

        Args:
            perf_data: {策略名: [日收益率列表]}，长度需一致（滚动窗口）

        Returns:
            {策略A: {策略B: r_AB, ...}, ...}
        """
        names = list(perf_data.keys())
        n = len(names)
        if n < 2:
            self._state.correlation_matrix = {}
            return {}

        matrix = {}
        for i in range(n):
            matrix[names[i]] = {}
            for j in range(n):
                if i == j:
                    matrix[names[i]][names[j]] = 1.0
                    continue
                r = self._pearson_corr(perf_data[names[i]], perf_data[names[j]])
                matrix[names[i]][names[j]] = round(r, 4)

        self._state.correlation_matrix = matrix
        self._save_correlation_cache()
        return matrix

    def check_correlation_alerts(self) -> List[str]:
        """检查相关性告警（PM-003）"""
        alerts = []
        mat = self._state.correlation_matrix
        warn_thr = self.cfg["risk"]["correlation_threshold_warning"]
        force_thr = self.cfg["risk"]["correlation_threshold_force"]
        for a, row in mat.items():
            for b, r in row.items():
                if a >= b:  # 避免重复（对称矩阵只处理上三角）
                    continue
                if r >= force_thr:
                    alerts.append(f"🔴 高相关({a}↔{b}: r={r:.2f}≥{force_thr}) → 需强制再平衡")
                elif r >= warn_thr:
                    alerts.append(f"🟡 相关告警({a}↔{b}: r={r:.2f}≥{warn_thr})")
        self._state.alerts = alerts
        return alerts

    def _pearson_corr(self, x: List[float], y: List[float]) -> float:
        """计算皮尔逊相关系数"""
        n = min(len(x), len(y))
        if n < 3:
            return 0.0
        x, y = x[:n], y[:n]
        mx = sum(x) / n
        my = sum(y) / n
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den = math.sqrt(sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y))
        return num / max(den, 1e-10)

    # ═══════════════════════════════════════════════════
    # PM-004：策略熔断机制
    # ═══════════════════════════════════════════════════

    def _load_circuit_state(self) -> None:
        """加载熔断状态"""
        if self.CIRCUIT_FILE.exists():
            try:
                data = json.loads(self.CIRCUIT_FILE.read_text(encoding="utf-8"))
                self._circuit_breakers = data.get("breakers", {})
            except Exception:
                self._circuit_breakers = {}

    def _save_circuit_state(self) -> None:
        """保存熔断状态"""
        try:
            data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "breakers": self._circuit_breakers,
            }
            self.CIRCUIT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def check_strategy_circuit_breaker(self, name: str) -> bool:
        """检查并执行策略熔断（PM-004）

        Returns: True=熔断触发, False=正常
        """
        s = self._strategies.get(name)
        if not s or not s.enabled:
            return False
        dd = s.metrics.get("drawdown", 0)
        threshold = s.max_drawdown

        # 熔断逻辑
        if dd <= threshold:
            self._circuit_breakers[name] = True
            s.status = "circuit_triggered"
            self._save_circuit_state()
            self._save_strategies()
            return True

        # 恢复逻辑
        recovery_ratio = self.cfg["risk"]["circuit_breaker_recovery"]
        if self._circuit_breakers.get(name, False) and dd > threshold * recovery_ratio:
            self._circuit_breakers[name] = False
            s.status = "active"
            self._save_circuit_state()
            self._save_strategies()
            # 恢复时自动触发再平衡
            return False

        return False

    def check_all_circuit_breakers(self) -> List[str]:
        """检查所有策略熔断，返回触发列表"""
        triggered = []
        for name in list(self._strategies.keys()):
            if self.check_strategy_circuit_breaker(name):
                triggered.append(name)
        return triggered

    # ═══════════════════════════════════════════════════
    # PM-005：总仓位/容量/跨策略约束
    # ═══════════════════════════════════════════════════

    def check_portfolio_risk(self,
                             positions: Dict[str, List[Dict]],
                             pool_manager=None) -> List[str]:
        """组合风控检查（v2增强版，PM-005）

        覆盖：总持仓数、总仓位比例、单策略容量、跨策略暴露

        Args:
            positions: {策略名: [持仓标的dict]}
            pool_manager: 可选PoolManager实例，用于跨池检查

        Returns:
            告警列表
        """
        alerts = []

        # 总持仓数
        total_pos = sum(len(v) for v in positions.values())
        max_total = self.cfg["risk"]["max_total_positions"]
        if total_pos > max_total:
            alerts.append(f"总持仓超限({total_pos}>{max_total})")

        # 单策略容量
        for name, stocks in positions.items():
            cfg = self._strategies.get(name)
            if cfg and len(stocks) > cfg.max_positions:
                alerts.append(f"{name}持仓超限({len(stocks)}>{cfg.max_positions})")

            # 单策略回撤
            if cfg and cfg.metrics.get("drawdown", 0) < cfg.max_drawdown:
                alerts.append(f"{name}回撤超限({cfg.metrics.get('drawdown',0):.1f}%<{cfg.max_drawdown:.1f}%)")

        # 组合回撤
        if self._state.drawdown < -10:
            alerts.append(f"组合回撤熔断({self._state.drawdown:.1f}%)")

        # 跨策略暴露检查 (PM-005 新增)
        cross_limit = self.cfg["risk"]["cross_strategy_position_limit"]
        code_counts: Dict[str, Dict] = {}
        for s_name, stocks in positions.items():
            for stk in stocks:
                code = str(stk.get("代码", stk.get("股票代码", "")))
                if not code:
                    continue
                if code not in code_counts:
                    code_counts[code] = {"name": stk.get("股票名称", stk.get("name", "")),
                                          "strategies": [], "total_pct": 0.0}
                code_counts[code]["strategies"].append(s_name)
                code_counts[code]["total_pct"] += float(stk.get("仓位", stk.get("仓位比例", 0)))

        for code, info in code_counts.items():
            if info["total_pct"] > cross_limit:
                alerts.append(f"跨策略暴露超限({info['name']}/{code}: "
                              f"{info['total_pct']:.1f}%>{cross_limit}%, "
                              f"涉及:{','.join(info['strategies'])})")

        # 熔断检查
        circuit_triggered = self.check_all_circuit_breakers()
        for ct_name in circuit_triggered:
            alerts.append(f"{ct_name}回撤熔断触发")

        self._state.alerts = alerts
        return alerts

    def check_capacity_limits(self, strategy_name: str,
                              current_stocks: List[Dict]) -> List[str]:
        """检查策略容量上限（PM-005）"""
        alerts = []
        s = self._strategies.get(strategy_name)
        if not s:
            return alerts
        if len(current_stocks) > s.max_positions:
            alerts.append(f"{strategy_name}持仓数({len(current_stocks)})超过上限({s.max_positions})")
        # 资金容量
        if s.allocation > s.max_allocation:
            alerts.append(f"{strategy_name}分配比例({s.allocation:.1%})超过上限({s.max_allocation:.1%})")
        return alerts

    # ═══════════════════════════════════════════════════
    # PM-006：定期再平衡 + 平滑调仓
    # ═══════════════════════════════════════════════════

    def check_rebalance(self, current_allocation: Dict[str, float],
                        target_allocation: Dict[str, float]) -> Tuple[bool, Dict[str, float]]:
        """检查是否需要再平衡（偏离度触发）"""
        adjustments = {}
        needs_rebalance = False
        for name, target in target_allocation.items():
            current = current_allocation.get(name, 0)
            deviation = abs(current - target)
            cfg = self._strategies.get(name)
            threshold = cfg.rebalance_threshold if cfg else 0.05
            if deviation > threshold:
                needs_rebalance = True
                adjustments[name] = target - current
        return needs_rebalance, adjustments

    def periodic_rebalance(self, force_interval: Optional[str] = None) -> Tuple[bool, str]:
        """定期再平衡检查与执行（PM-006）

        Args:
            force_interval: 可选，覆盖配置的周期性检查（"daily"/"weekly"/"monthly"）

        Returns:
            (是否执行了再平衡, 说明)
        """
        # 检查频率
        interval = force_interval or self.cfg["rebalance"]["periodic_interval"]
        rc = self.cfg["rebalance"]
        if not rc.get("periodic", True):
            return False, "定期再平衡已关闭"

        # 防抖：同一天内不重复触发
        now = datetime.now()
        if self._last_rebalance_time:
            last = self._last_rebalance_time
            if interval == "daily" and last.date() == now.date():
                return False, "今日已执行再平衡"
            if interval == "weekly" and (now - last).days < 7:
                return False, "本周已执行再平衡"
            if interval == "monthly" and (now - last).days < 28:
                return False, "本月已执行再平衡"

        # 检查指定日期（weekly→周一, monthly→1日）
        if interval == "weekly":
            target_day = rc.get("periodic_day", "Monday")
            weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            if weekdays[now.weekday()] != target_day:
                return False, f"非{target_day}，跳过定期再平衡"
        elif interval == "monthly":
            if now.day != 1:
                return False, "非每月1日，跳过定期再平衡"

        # 执行再平衡
        method = self.cfg.get("default_allocation_method", "risk_parity")
        result = self.rebalance(method=method)
        self._last_rebalance_time = now
        return True, f"定期再平衡完成({interval})"

    def _smooth_adjustment(self, target_alloc: Dict[str, float],
                           max_turnover: Optional[float] = None) -> Dict[str, float]:
        """平滑调仓：限制单次调仓幅度（PM-006）

        一次最多调整 max_turnover 比例的资金
        """
        max_to = max_turnover or self.cfg["rebalance"]["max_turnover_per_trade"]
        current = {s.name: s.allocation for s in self.get_enabled_strategies()}

        total_adjust = sum(abs(target_alloc.get(k, 0) - current.get(k, 0)) for k in set(list(target_alloc) + list(current)))
        if total_adjust <= max_to:
            return target_alloc  # 无需平滑

        clipped = {}
        for name, target in target_alloc.items():
            cur = current.get(name, 0)
            diff = target - cur
            # 按比例压缩
            clip_ratio = max_to / max(total_adjust, 0.001)
            clipped[name] = cur + diff * clip_ratio

        # 归一化
        total = sum(clipped.values())
        if total > 0:
            clipped = {k: v / total for k, v in clipped.items()}
        return clipped

    def rebalance(self, method: str = "periodic", force: bool = False,
                  smooth: bool = True, **kwargs) -> Dict[str, float]:
        """执行再平衡（增强版，PM-006）

        Args:
            method: 分配方法
            force: 强制再平衡（跳过偏离度检查）
            smooth: 是否平滑调仓

        Returns: {策略名: 新分配比例}
        """
        target = self.allocate(method, **kwargs)
        if not target:
            return {}

        # 平滑
        if smooth:
            target = self._smooth_adjustment(target)

        current = {s.name: s.allocation for s in self.get_enabled_strategies()}
        needs, _ = self.check_rebalance(current, target)

        if needs or force:
            for name, pct in target.items():
                if name in self._strategies:
                    self.update_strategy(name, allocation=pct)
            self._state.allocation_method = method
            self._state.last_rebalance = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_snapshot(target)
            self._last_rebalance_time = datetime.now()

        return target

    # ═══════════════════════════════════════════════════
    # PM-007：策略优胜劣汰
    # ═══════════════════════════════════════════════════

    def survival_competition(self, force: bool = False) -> List[str]:
        """策略优胜劣汰（PM-007）

        每 N 天评估一次，按综合评分淘汰末位策略，释放资金重新分配。

        综合评分 = Sharpe_norm × 0.5 + WinRate_norm × 0.3 + (1-Drawdown_norm) × 0.2

        Returns: 被淘汰的策略名列表
        """
        config = self.cfg["performance"]
        interval = config.get("survival_competition_days", 30)
        elimination_count = config.get("survival_elimination_count", 1)

        strategies = self.get_enabled_strategies()
        if len(strategies) <= elimination_count + 1:
            return []  # 策略太少，不淘汰

        # 评分归一化
        sharpe_vals = [s.metrics.get("sharpe", 0) for s in strategies]
        win_vals = [s.metrics.get("win_rate", 0) for s in strategies]
        dd_vals = [s.metrics.get("drawdown", 0) for s in strategies]

        def norm(vals):
            mn, mx = min(vals), max(vals)
            return [(v - mn) / max(mx - mn, 1e-6) for v in vals]

        sharpe_n = norm(sharpe_vals)
        win_n = norm(win_vals)
        dd_n = norm(dd_vals)

        # 综合评分
        scored = []
        for i, s in enumerate(strategies):
            score = sharpe_n[i] * 0.5 + win_n[i] * 0.3 + (1 - dd_n[i]) * 0.2
            scored.append((score, s.name))

        scored.sort(key=lambda x: x[0])  # 升序
        eliminated = [name for _, name in scored[:elimination_count]]

        # 执行淘汰：移除策略
        for name in eliminated:
            self.remove_strategy(name)

        # 释放资金重新分配
        if eliminated:
            self.rebalance(force=True)

        return eliminated

    # ═══════════════════════════════════════════════════
    # PM-008 + PM-009：组合绩效分析
    # ═══════════════════════════════════════════════════

    def update_metrics(self, name: str, **metrics) -> None:
        """更新策略指标"""
        if name in self._strategies:
            self._strategies[name].metrics.update(metrics)
            self._save_strategies()

    def calc_portfolio_return(self, strategy_returns: Dict[str, float]) -> float:
        """计算组合层面收益（PM-008）

        R_portfolio = Σ(w_i × R_i)
        """
        total = 0.0
        for s in self.get_enabled_strategies():
            r = strategy_returns.get(s.name, 0)
            total += s.allocation * r
        self._state.daily_pnl = total
        self._state.cumulative_pnl += total
        return total

    def calc_volatility(self, daily_returns: List[float]) -> float:
        """计算波动率（日收益率年化）"""
        if len(daily_returns) < 5:
            return 0.0
        mean_r = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
        return math.sqrt(var * 252) * 100  # 年化百分比

    def calc_risk_metrics(self,
                          daily_returns: List[float],
                          benchmark_return: float = 0) -> Dict[str, float]:
        """计算组合风险指标（PM-008）

        Returns:
            {sharpe, sortino, calmar, var_95, volatility, max_drawdown}
        """
        if not daily_returns:
            return {"sharpe": 0, "sortino": 0, "calmar": 0, "var_95": 0, "volatility": 0, "max_drawdown": 0}

        n = len(daily_returns)
        risk_free = self.cfg["performance"]["risk_free_rate"] / 100.0 / 252  # 日化无风险

        # 收益率
        mean_r = sum(daily_returns) / n

        # 波动率（年化）
        var = sum((r - mean_r) ** 2 for r in daily_returns) / n
        vol = math.sqrt(var * 252) if var > 1e-10 else 0.001

        # 夏普
        excess = mean_r - risk_free
        sharpe = (excess / max(vol / math.sqrt(252), 1e-6)) * math.sqrt(252) if vol > 0 else 0

        # 索提诺（只考虑下行波动）
        downside = [r - risk_free for r in daily_returns if r < risk_free]
        if downside:
            down_var = sum(d ** 2 for d in downside) / len(downside)
            down_vol = math.sqrt(down_var * 252)
            sortino = (mean_r - risk_free) / max(down_vol / math.sqrt(252), 1e-6) * math.sqrt(252) if down_vol > 0 else 0
        else:
            sortino = sharpe

        # 最大回撤
        peak = daily_returns[0]
        max_dd = 0.0
        cum = 0.0
        for r in daily_returns:
            cum += r
            if cum > peak:
                peak = cum
            dd = (cum - peak) / max(abs(peak), 0.01)
            max_dd = min(max_dd, dd)

        # 卡玛
        annual_return = mean_r * 252
        calmar = annual_return / max(abs(max_dd), 0.01) if max_dd < 0 else annual_return * 100

        # VaR 95%（历史模拟法）
        sorted_r = sorted(daily_returns)
        idx = max(0, int(n * 0.05) - 1)
        var_95 = sorted_r[idx] * 100  # 转为百分比

        result = {
            "sharpe": round(sharpe, 4),
            "sortino": round(sortino, 4),
            "calmar": round(calmar, 4),
            "var_95": round(var_95, 2),
            "volatility": round(vol * 100, 2),  # %
            "max_drawdown": round(max_dd * 100, 2),  # %
        }

        self._state.sharpe_ratio = result["sharpe"]
        self._state.sortino_ratio = result["sortino"]
        self._state.calmar_ratio = result["calmar"]
        self._state.var_95 = result["var_95"]
        self._state.volatility = result["volatility"]
        self._state.drawdown = result["max_drawdown"]
        return result

    def calc_attribution(self, strategy_returns: Dict[str, float]) -> Dict[str, float]:
        """归因分析：计算各策略对组合收益的贡献（PM-009）

        绝对贡献 = w_i × R_i
        相对贡献 = w_i × R_i / R_portfolio

        Returns: {策略名: 绝对贡献(%)}
        """
        total_return = 0.0
        contributions = {}
        for s in self.get_enabled_strategies():
            r = strategy_returns.get(s.name, 0)
            contribution = s.allocation * r
            contributions[s.name] = round(contribution * 100, 4)  # 转为百分比
            total_return += contribution

        contributions["_portfolio_total"] = round(total_return * 100, 4)
        contributions["_risk_free"] = self.cfg["performance"]["risk_free_rate"]

        # 相对贡献（百分比）
        if abs(total_return) > 1e-6:
            for k in list(contributions.keys()):
                if not k.startswith("_"):
                    contributions[f"{k}_relative"] = round(
                        (strategy_returns.get(k, 0) * self._strategies[k].allocation) / total_return * 100, 2
                    ) if k in self._strategies else 0

        return contributions

    def calc_strategy_contribution(self, name: str,
                                    strategy_return: float) -> Dict[str, Any]:
        """单策略贡献度拆解（PM-009）"""
        s = self._strategies.get(name)
        if not s:
            return {}
        abs_contrib = s.allocation * strategy_return * 100  # %
        return {
            "strategy": name,
            "allocation_pct": s.allocation * 100,
            "return_pct": strategy_return * 100,
            "absolute_contribution_pct": round(abs_contrib, 4),
            "sharpe": s.metrics.get("sharpe", 0),
            "win_rate": s.metrics.get("win_rate", 0),
            "status": s.derived_status,
        }

    def generate_performance_report(self, daily_returns: List[float],
                                    strategy_returns: Dict[str, float],
                                    pool_manager=None) -> Dict[str, Any]:
        """生成组合绩效报告（PM-008+PM-009）"""
        report = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_capital": self._state.total_capital,
            "used_capital": self._state.used_capital,
            "free_capital": self._state.free_capital,
        }

        # 组合收益
        report["portfolio_return"] = self.calc_portfolio_return(strategy_returns)

        # 风险指标
        report["risk_metrics"] = self.calc_risk_metrics(daily_returns)

        # 归因分析
        report["attribution"] = self.calc_attribution(strategy_returns)

        # 策略列表
        report["strategies"] = []
        for s in self.list_strategies():
            report["strategies"].append({
                "name": s.name,
                "enabled": s.enabled,
                "status": s.derived_status,
                "allocation": s.allocation,
                "version": s.version,
                "metrics": dict(s.metrics),
            })

        # 相关性矩阵
        report["correlation_matrix"] = self._state.correlation_matrix

        # 告警
        report["alerts"] = self._state.alerts

        # 保存报告
        report_file = self.REPORT_DIR / f"portfolio_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return report

    # ═══════════════════════════════════════════════════
    # 持久化
    # ═══════════════════════════════════════════════════

    def _load_strategies(self) -> None:
        """加载策略列表"""
        for f in self.STRATEGY_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                # 版本历史兼容（旧版无versions字段）
                if "versions" not in data:
                    data["versions"] = []
                cfg = StrategyConfig(**data)
                self._strategies[cfg.name] = cfg
            except Exception:
                continue

    def _save_strategies(self) -> None:
        """保存策略列表"""
        for cfg in self._strategies.values():
            path = self.STRATEGY_DIR / f"{cfg.name}.json"
            try:
                path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    def _load_state(self) -> None:
        """加载组合状态"""
        if self.PORTFOLIO_FILE.exists():
            try:
                data = json.loads(self.PORTFOLIO_FILE.read_text(encoding="utf-8"))
                # alerts 字段兼容
                if "alerts" not in data:
                    data["alerts"] = []
                self._state = PortfolioState(**data)
            except Exception:
                self._state = PortfolioState()

    def _save_snapshot(self, allocation: Dict[str, float]) -> None:
        """保存组合快照"""
        self._state.allocated = allocation
        self._state.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.PORTFOLIO_FILE.write_text(
                json.dumps(asdict(self._state), ensure_ascii=False, indent=2), encoding="utf-8")
            # 历史快照
            history_file = self.HISTORY_DIR / f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            history_file.write_text(
                json.dumps(asdict(self._state), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def save_checkpoint(self) -> None:
        """保存开发/运行checkpoint"""
        cp = {
            "checkpoint_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strategy_count": len(self._strategies),
            "enabled_count": len(self.get_enabled_strategies()),
            "state_summary": {
                "total_capital": self._state.total_capital,
                "used_capital": self._state.used_capital,
                "cumulative_pnl": self._state.cumulative_pnl,
                "drawdown": self._state.drawdown,
            },
        }
        try:
            self.CHECKPOINT_FILE.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


# ═══════════════════════════════════════════════════
# CLI入口（PM-011）
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="多策略组合管理器 v2.0")
    sub = parser.add_subparsers(dest="action", help="子命令")

    # list
    p_list = sub.add_parser("list", help="列出所有策略")

    # register
    p_reg = sub.add_parser("register", help="注册新策略")
    p_reg.add_argument("--name", required=True)
    p_reg.add_argument("--allocation", type=float, default=0.1)
    p_reg.add_argument("--desc", default="")
    p_reg.add_argument("--max-dd", type=float, default=-15.0)
    p_reg.add_argument("--max-pos", type=int, default=5)

    # enable/disable/toggle
    for cmd in ["enable", "disable", "toggle", "remove", "status", "history"]:
        sp = sub.add_parser(cmd, help=f"{cmd} 策略")
        sp.add_argument("--name", required=True)

    # update
    p_upd = sub.add_parser("update", help="更新策略参数")
    p_upd.add_argument("--name", required=True)
    p_upd.add_argument("--allocation", type=float)
    p_upd.add_argument("--max-dd", type=float)
    p_upd.add_argument("--max-pos", type=int)
    p_upd.add_argument("--desc")

    # rollback
    p_rb = sub.add_parser("rollback", help="回滚策略版本")
    p_rb.add_argument("--name", required=True)
    p_rb.add_argument("--version", required=True)

    # allocate
    p_alloc = sub.add_parser("allocate", help="分配资金")
    p_alloc.add_argument("--method", choices=["equal", "fixed", "risk_parity", "kelly", "performance"], default="equal")

    # rebalance
    p_reb = sub.add_parser("rebalance", help="执行再平衡")
    p_reb.add_argument("--method", choices=["equal", "fixed", "risk_parity", "kelly", "performance"], default="equal")
    p_reb.add_argument("--force", action="store_true")

    # periodic
    p_per = sub.add_parser("periodic", help="定期再平衡检查")

    # survival
    p_sur = sub.add_parser("survival", help="策略优胜劣汰")
    p_sur.add_argument("--force", action="store_true")

    # risk
    p_risk = sub.add_parser("risk", help="组合风控检查")

    # report
    p_rep = sub.add_parser("report", help="生成绩效报告")

    args = parser.parse_args()
    pm = PortfolioManager()

    if args.action == "list":
        print(f"{'状态':>4} {'策略名':<20} {'比例':>6} {'版本':>6} {'收益':>8} {'回撤':>7} {'夏普':>6} {'状态':<18}")
        print("-" * 80)
        for s in pm.list_strategies():
            st = pm.get_strategy_status(s.name)
            icon = "✅" if s.enabled else "⏸️"
            print(f" {icon} {s.name:<20} {s.allocation*100:>5.0f}% {s.version:>6} "
                  f"{st['total_return']:>7.1f}% {st['drawdown']:>6.1f}% {st['sharpe']:>5.2f} {st['status']:<18}")

    elif args.action == "register":
        cfg = StrategyConfig(name=args.name, allocation=args.allocation,
                             description=args.desc, max_drawdown=args.max_dd,
                             max_positions=args.max_pos)
        ok = pm.register_strategy(cfg)
        print(f"{'✅' if ok else '❌'} 注册策略: {args.name}")

    elif args.action == "enable":
        print(f"{'✅' if pm.enable_strategy(args.name) else '❌'} 启用: {args.name}")
    elif args.action == "disable":
        print(f"{'✅' if pm.disable_strategy(args.name) else '❌'} 停用: {args.name}")
    elif args.action == "toggle":
        print(f"{'✅' if pm.toggle_strategy(args.name) else '❌'} 切换: {args.name}")
    elif args.action == "remove":
        print(f"{'✅' if pm.remove_strategy(args.name) else '❌'} 移除: {args.name}")

    elif args.action == "status":
        st = pm.get_strategy_status(args.name)
        if not st:
            print(f"❌ 未找到策略: {args.name}")
        else:
            print(f"=== {args.name} 状态 ===")
            for k, v in st.items():
                print(f"  {k}: {v}")

    elif args.action == "history":
        versions = pm.get_version_history(args.name)
        if not versions:
            print(f"无版本历史: {args.name}")
        else:
            print(f"=== {args.name} 版本历史 ===")
            for v in versions:
                print(f"  v{v['version']} ({v.get('timestamp','')}) "
                      f"alloc={v.get('allocation',0)*100:.0f}% "
                      f"max_dd={v.get('max_drawdown',0)}%")

    elif args.action == "rollback":
        ok = pm.rollback_version(args.name, args.version)
        print(f"{'✅' if ok else '❌'} 回滚 {args.name} 到 v{args.version}")

    elif args.action == "update":
        kw = {}
        if args.allocation is not None:
            kw["allocation"] = args.allocation
        if args.max_dd is not None:
            kw["max_drawdown"] = args.max_dd
        if args.max_pos is not None:
            kw["max_positions"] = args.max_pos
        if args.desc is not None:
            kw["description"] = args.desc
        ok = pm.update_strategy(args.name, **kw)
        print(f"{'✅' if ok else '❌'} 更新: {args.name} → {kw}")

    elif args.action == "allocate":
        result = pm.allocate(args.method) if args.method else pm.allocate("equal")
        print(f"资金分配 ({args.method}):")
        for k, v in sorted(result.items(), key=lambda x: -x[1]):
            print(f"  {k:<20} {v*100:>5.1f}%")

    elif args.action == "rebalance":
        result = pm.rebalance(method=args.method, force=args.force)
        print(f"再平衡完成 ({'强制' if args.force else '常规'}):")
        for k, v in sorted(result.items(), key=lambda x: -x[1]):
            print(f"  {k:<20} {v*100:>5.1f}%")

    elif args.action == "periodic":
        executed, msg = pm.periodic_rebalance()
        print(f"定期再平衡: {'✅' if executed else '⏸️'} {msg}")

    elif args.action == "survival":
        eliminated = pm.survival_competition(force=args.force)
        if eliminated:
            print(f"优胜劣汰淘汰: {', '.join(eliminated)}")
        else:
            print("本次无淘汰")

    elif args.action == "risk":
        alerts = pm.check_portfolio_risk(
            {s.name: [] for s in pm.get_enabled_strategies()})
        if alerts:
            print("=== 风控告警 ===")
            for a in alerts:
                print(f"  ⚠️ {a}")
        else:
            print("✅ 风控检查通过")

    elif args.action == "report":
        report = pm.generate_performance_report([], {})
        print(f"=== 组合绩效报告 ===")
        print(f"  总资金: {report['total_capital']:,.0f}")
        print(f"  累计收益: {report.get('portfolio_return', 0)*100:.2f}%")
        rm = report.get("risk_metrics", {})
        print(f"  夏普: {rm.get('sharpe', 0):.2f} | 索提诺: {rm.get('sortino', 0):.2f}")
        print(f"  年化波动: {rm.get('volatility', 0):.1f}% | VaR95: {rm.get('var_95', 0):.1f}%")
        print(f"  最大回撤: {rm.get('max_drawdown', 0):.1f}%")
        print(f"  告警: {len(report.get('alerts', []))} 条")

    else:
        # 默认显示组合状态
        print(f"=== 组合状态 ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===")
        print(f"  总资金: {pm._state.total_capital:,.0f}")
        print(f"  已用: {pm._state.used_capital:,.0f} | 可用: {pm._state.free_capital:,.0f}")
        print(f"  累计盈亏: {pm._state.cumulative_pnl:,.2f}")
        print(f"  回撤: {pm._state.drawdown:.2f}%")
        print(f"  夏普: {pm._state.sharpe_ratio:.2f}")
        print(f"  分配方法: {pm._state.allocation_method}")
        print(f"  策略数: {len(pm.list_strategies())} (启用{len(pm.get_enabled_strategies())})")
        for s in pm.list_strategies():
            st = pm.get_strategy_status(s.name)
            print(f"  {'✅' if s.enabled else '⏸️'} {s.name:<20} {s.allocation*100:>5.0f}% "
                  f"v{s.version:<5} {st['status']:<18} "
                  f"收益{st['total_return']:>6.1f}% 回撤{st['drawdown']:>5.1f}%")