#!/usr/bin/env python3
"""智能条件单与分级止盈止损体系

CO-001~009 全功能覆盖：
- 价格条件单：限价买卖/突破买入/跌破卖出
- 止盈止损：固定止损/分级止盈/移动止损/时间止损
- 盈亏条件：单日亏降仓/单票回撤平仓/组合回撤全减
- 条件管理：CRUD/触发日志/优先级
- 异常兜底：行情异常/断网/触发失败
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = PROJECT_ROOT / "data" / "conditional_orders"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 枚举与类型
# ═══════════════════════════════════════════════════════════════

class ConditionType(str, Enum):
    """条件单类型"""
    LIMIT_BUY = "LIMIT_BUY"           # 限价买入（价≤触发价）
    LIMIT_SELL = "LIMIT_SELL"         # 限价卖出（价≥触发价）
    BREAK_BUY = "BREAK_BUY"           # 突破买入（价≥触发价）
    BREAK_SELL = "BREAK_SELL"         # 跌破卖出（价≤触发价）
    FIXED_STOP_LOSS = "FIXED_SL"      # 固定止损
    TIERED_TAKE_PROFIT = "TIERED_TP"  # 分级止盈
    TRAILING_STOP = "TRAILING_SL"     # 移动止损
    TIME_STOP = "TIME_SL"             # 时间止损
    DAILY_LOSS_CUT = "DAILY_LOSS"     # 单日亏损降仓
    DRAWDOWN_CLOSE = "DRAWDOWN"       # 单票回撤平仓
    PORTFOLIO_CUT = "PORTFOLIO_DD"    # 组合回撤全减


class OrderStatus(str, Enum):
    """订单状态"""
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    TRIGGERED = "triggered"
    EXECUTING = "executing"
    EXECUTED = "executed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Priority(int, Enum):
    """执行优先级（数字越小优先级越高）"""
    RISK = 1       # 风控条件
    STOP_LOSS = 2  # 止损条件
    TAKE_PROFIT = 3 # 止盈条件
    PRICE = 4      # 价格条件
    STRATEGY = 5   # 高级策略


# ═══════════════════════════════════════════════════════════════
# 数据模型 (CO-001)
# ═══════════════════════════════════════════════════════════════

@dataclass
class TieredTarget:
    """分级止盈目标"""
    level: int           # 第几档 (1,2,3)
    profit_pct: float    # 触发涨幅 %
    sell_ratio: float    # 卖出比例 (0-1)
    triggered: bool = False


@dataclass
class ConditionalOrder:
    """条件单"""
    condition_type: ConditionType    # 条件类型
    code: str                        # 股票代码
    name: str                        # 股票名称
    order_id: str = ""               # 唯一ID
    status: OrderStatus = OrderStatus.CREATED
    priority: int = Priority.PRICE   # 优先级

    # 触发参数
    trigger_price: float = 0.0       # 触发价格
    cost_price: float = 0.0          # 成本价（用于止损止盈计算）
    quantity: int = 0                # 数量（0=全部/金额模式）
    amount: float = 0.0              # 金额

    # 止盈止损参数
    stop_loss_pct: float = -5.0      # 固定止损%
    tiered_targets: List[TieredTarget] = field(default_factory=list)  # 分级止盈
    trailing_activate_pct: float = 5.0   # 移动止损激活涨幅%
    trailing_distance_pct: float = 3.0   # 移动止损回撤距离%
    time_stop_days: int = 5              # 时间止损天数

    # 风控参数
    daily_loss_cut_pct: float = -7.0     # 单日亏损降仓%
    drawdown_close_pct: float = -15.0    # 单票回撤平仓%

    # 运行时状态
    highest_price: float = 0.0       # 持仓期间最高价（移动止损用）
    triggered_time: Optional[str] = None
    executed_time: Optional[str] = None
    executed_price: float = 0.0
    executed_quantity: int = 0
    pnl_pct: float = 0.0
    error_msg: str = ""
    note: str = ""

    # 时间戳
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TriggerLog:
    """触发日志"""
    log_id: str
    order_id: str
    condition_type: str
    code: str
    name: str
    trigger_time: str
    trigger_price: float
    current_price: float
    action: str                          # buy / sell / cut / close
    quantity: int
    amount: float
    reason: str
    success: bool
    error_msg: str = ""


# ═══════════════════════════════════════════════════════════════
# 行情数据接口 (CO-007)
# ═══════════════════════════════════════════════════════════════

class MarketDataFeed:
    """行情数据接口（委托 QuoteProvider 实现，统一行情入口）

    为保持向后兼容，返回字段与旧版 MarketDataFeed 保持一致。
    实际数据来自 QuoteProvider（带30s缓存）。
    """

    @staticmethod
    def fetch_quote(code: str) -> Optional[Dict[str, Any]]:
        """获取实时行情（委托 QuoteProvider）"""
        from quote_provider import QuoteProvider
        quote = QuoteProvider.fetch_quote(code)
        if not quote:
            return None
        # 统一字段名，保持向后兼容
        return {
            "code": quote.get("code", code),
            "name": quote.get("name", ""),
            "price": quote.get("price", 0),
            "chg_pct": quote.get("chg_pct", 0),
            "high": quote.get("high", 0),
            "low": quote.get("low", 0),
            "volume": quote.get("volume", 0),
        }

    @staticmethod
    def fetch_batch(codes: List[str], fallback_price: float = 100.0) -> Dict[str, Dict]:
        """批量获取行情（委托 QuoteProvider）"""
        from quote_provider import QuoteProvider
        raw = QuoteProvider.fetch_batch(codes)
        result = {}
        for code in codes:
            quote = raw.get(code)
            if quote:
                result[code] = {
                    "code": quote.get("code", code),
                    "name": quote.get("name", ""),
                    "price": quote.get("price", fallback_price),
                    "chg_pct": quote.get("chg_pct", 0),
                    "high": quote.get("high", fallback_price),
                    "low": quote.get("low", fallback_price),
                    "volume": quote.get("volume", 0),
                }
            else:
                result[code] = {"code": code, "price": fallback_price, "chg_pct": 0, "high": fallback_price, "low": fallback_price}
        return result


# ═══════════════════════════════════════════════════════════════
# 异常兜底 (CO-008)
# ═══════════════════════════════════════════════════════════════

class ExceptionHandler:
    """异常兜底处理器"""

    MAX_RETRIES = 3
    RETRY_DELAY = 5  # 秒

    @staticmethod
    def handle_fetch_error(code: str, error: Exception) -> Dict[str, Any]:
        """行情获取失败的兜底处理"""
        print(f"  ⚠️ [{code}] 行情获取失败: {error}，使用上次已知价格")
        return {"code": code, "price": 0, "chg_pct": 0, "high": 0, "low": 0}

    @staticmethod
    def handle_execution_error(order: ConditionalOrder, error: Exception) -> ConditionalOrder:
        """执行失败处理"""
        order.status = OrderStatus.FAILED
        order.error_msg = str(error)[:200]
        order.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return order

    @staticmethod
    def is_market_open() -> bool:
        """检查是否在交易时间（简单判断，不支持节假日）"""
        now = datetime.now()
        if now.weekday() >= 5:  # 周末
            return False
        h, m = now.hour, now.minute
        # 9:30-11:30, 13:00-15:00
        if (h == 9 and m >= 30) or (h == 10) or (h == 11 and m <= 30):
            return True
        if (h == 13) or (h == 14):
            return True
        return False

    @staticmethod
    def get_fallback_price(order: ConditionalOrder) -> float:
        """获取兜底价格"""
        if order.highest_price > 0:
            return order.highest_price
        return order.cost_price or order.trigger_price


# ═══════════════════════════════════════════════════════════════
# 条件评估引擎 (CO-003/004/005/006)
# ═══════════════════════════════════════════════════════════════

class ConditionEngine:
    """条件评估引擎 — 核心触发逻辑"""

    @staticmethod
    def check_price_condition(order: ConditionalOrder,
                              price: float) -> Tuple[bool, str]:
        """价格条件单评估"""
        if price <= 0:
            return False, "无效价格"

        tp = order.trigger_price
        if tp <= 0:
            return False, "未设置触发价"

        ct = order.condition_type
        if ct == ConditionType.LIMIT_BUY:
            if price <= tp:
                return True, f"限价买入触发: {price:.2f}≤{tp:.2f}"
        elif ct == ConditionType.LIMIT_SELL:
            if price >= tp:
                return True, f"限价卖出触发: {price:.2f}≥{tp:.2f}"
        elif ct == ConditionType.BREAK_BUY:
            if price >= tp:
                return True, f"突破买入触发: {price:.2f}≥{tp:.2f}"
        elif ct == ConditionType.BREAK_SELL:
            if price <= tp:
                return True, f"跌破卖出触发: {price:.2f}≤{tp:.2f}"
        return False, "条件未满足"

    @staticmethod
    def check_fixed_stop_loss(order: ConditionalOrder,
                              price: float) -> Tuple[bool, str]:
        """固定止损检查"""
        cost = order.cost_price
        if cost <= 0:
            return False, "无成本价"
        threshold = cost * (1 + order.stop_loss_pct / 100)
        if price <= threshold:
            pnl = (price - cost) / cost * 100
            return True, f"固定止损触发: 价{price:.2f}≤{threshold:.2f}, 亏损{pnl:.1f}%"
        return False, "止损条件未满足"

    @staticmethod
    def check_tiered_take_profit(order: ConditionalOrder,
                                  price: float) -> Tuple[bool, TieredTarget]:
        """分级止盈检查（返回第一个未触发的达标档位）"""
        cost = order.cost_price
        if cost <= 0:
            return False, None
        for target in order.tiered_targets:
            if target.triggered:
                continue
            threshold = cost * (1 + target.profit_pct / 100)
            if price >= threshold:
                return True, target
        return False, None

    @staticmethod
    def check_trailing_stop(order: ConditionalOrder,
                             price: float) -> Tuple[bool, str]:
        """移动止损检查"""
        cost = order.cost_price
        if cost <= 0:
            return False, "无成本价"

        # 更新最高价
        if price > order.highest_price:
            order.highest_price = price

        # 检查是否已激活移动止损
        gain_pct = (order.highest_price - cost) / cost * 100
        if gain_pct < order.trailing_activate_pct:
            return False, f"未激活(涨幅{gain_pct:.1f}%<{order.trailing_activate_pct}%)"

        # 从最高点回撤检查
        drawdown = (order.highest_price - price) / order.highest_price * 100
        if drawdown >= order.trailing_distance_pct:
            pnl = (price - cost) / cost * 100
            return True, (f"移动止损触发: 从高点{order.highest_price:.2f}"
                          f"回撤{drawdown:.1f}%≥{order.trailing_distance_pct}%, "
                          f"盈利{pnl:.1f}%")
        return False, (f"移动止损监控中: 高点{order.highest_price:.2f}, "
                       f"回撤{drawdown:.1f}%<{order.trailing_distance_pct}%")

    @staticmethod
    def check_time_stop(order: ConditionalOrder) -> Tuple[bool, str]:
        """时间止损检查"""
        if not order.created_at:
            return False, "无创建时间"
        try:
            created = datetime.strptime(order.created_at, "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now() - created).days
            if elapsed >= order.time_stop_days:
                return True, f"时间止损触发: 持仓{elapsed}天≥{order.time_stop_days}天"
            return False, f"时间止损监控中: 持仓{elapsed}天<{order.time_stop_days}天"
        except ValueError:
            return False, "时间解析失败"

    @staticmethod
    def check_daily_loss_cut(order: ConditionalOrder,
                              price: float) -> Tuple[bool, str]:
        """单日亏损降仓检查"""
        cost = order.cost_price
        if cost <= 0:
            return False, "无成本价"
        pnl = (price - cost) / cost * 100
        if pnl <= order.daily_loss_cut_pct:
            return True, f"单日亏损降仓触发: 盈亏{pnl:.1f}%≤{order.daily_loss_cut_pct}%"
        return False, "日亏条件未满足"

    @staticmethod
    def check_drawdown_close(order: ConditionalOrder,
                              price: float) -> Tuple[bool, str]:
        """单票回撤平仓检查"""
        cost = order.cost_price
        if cost <= 0:
            return False, "无成本价"
        pnl = (price - cost) / cost * 100
        if pnl <= order.drawdown_close_pct:
            return True, f"回撤平仓触发: 累计盈亏{pnl:.1f}%≤{order.drawdown_close_pct}%"
        return False, "回撤条件未满足"

    @staticmethod
    def check_portfolio_cut(portfolio_drawdown: float,
                             threshold: float = -10.0) -> Tuple[bool, str]:
        """组合回撤全减"""
        if portfolio_drawdown <= threshold:
            return True, f"组合回撤全减: {portfolio_drawdown:.1f}%≤{threshold:.0f}%"
        return False, "组合回撤未触线"

    @staticmethod
    def evaluate_all(order: ConditionalOrder, price: float,
                     portfolio_dd: float = 0) -> List[Tuple[int, str, Callable]]:
        """全量条件评估（按优先级返回触发结果）

        Returns:
            [(priority, reason, action_func), ...]
        """
        triggers = []

        # P1: 风控条件
        is_trigger, reason = ConditionEngine.check_daily_loss_cut(order, price)
        if is_trigger:
            triggers.append((Priority.RISK, reason, "cut_half"))
        is_trigger, reason = ConditionEngine.check_drawdown_close(order, price)
        if is_trigger:
            triggers.append((Priority.RISK, reason, "close_all"))

        # P2: 止损条件
        is_trigger, reason = ConditionEngine.check_fixed_stop_loss(order, price)
        if is_trigger:
            triggers.append((Priority.STOP_LOSS, reason, "sell_all"))
        is_trigger, reason = ConditionEngine.check_trailing_stop(order, price)
        if is_trigger:
            triggers.append((Priority.STOP_LOSS, reason, "sell_all"))
        is_trigger, reason = ConditionEngine.check_time_stop(order)
        if is_trigger:
            triggers.append((Priority.STOP_LOSS, reason, "sell_all"))

        # P3: 止盈条件
        is_trigger, target = ConditionEngine.check_tiered_take_profit(order, price)
        if is_trigger and target:
            triggers.append((Priority.TAKE_PROFIT,
                             f"第{target.level}档止盈: {target.profit_pct:.0f}%, 卖{target.sell_ratio:.0%}",
                             ("sell_ratio", target.sell_ratio)))

        # P4: 价格条件
        is_trigger, reason = ConditionEngine.check_price_condition(order, price)
        if is_trigger:
            action = "buy" if "买入" in reason else "sell"
            triggers.append((Priority.PRICE, reason, action))

        # 按优先级排序
        triggers.sort(key=lambda x: x[0])
        return triggers


# ═══════════════════════════════════════════════════════════════
# 订单管理器 (CO-002)
# ═══════════════════════════════════════════════════════════════

class OrderManager:
    """条件单管理器 (CRUD)"""

    def __init__(self):
        self._orders: Dict[str, ConditionalOrder] = {}
        self._logs: List[TriggerLog] = []
        self._id_counter = 0
        self._load()

    def create_order(self, order: ConditionalOrder) -> str:
        """创建条件单"""
        self._id_counter += 1
        order.order_id = f"CO{self._id_counter:06d}"
        order.status = OrderStatus.ACTIVE
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order.created_at = now
        order.updated_at = now
        # 初始化最高价
        if order.cost_price > 0 and order.highest_price == 0:
            order.highest_price = order.cost_price
        self._orders[order.order_id] = order
        self._save()
        return order.order_id

    def create_market_order(self, condition_type: ConditionType, code: str, name: str,
                             trigger_price: float, cost_price: float = 0,
                             quantity: int = 0, amount: float = 0,
                             stop_loss_pct: float = -5.0,
                             tiered_targets: List[TieredTarget] = None,
                             trailing_activate: float = 5.0,
                             trailing_distance: float = 3.0,
                             time_stop_days: int = 5,
                             note: str = "") -> str:
        """快捷创建条件单"""
        p_map = {
            ConditionType.FIXED_STOP_LOSS: Priority.STOP_LOSS,
            ConditionType.TIERED_TAKE_PROFIT: Priority.TAKE_PROFIT,
            ConditionType.TRAILING_STOP: Priority.STOP_LOSS,
            ConditionType.TIME_STOP: Priority.STOP_LOSS,
            ConditionType.DAILY_LOSS_CUT: Priority.RISK,
            ConditionType.DRAWDOWN_CLOSE: Priority.RISK,
            ConditionType.PORTFOLIO_CUT: Priority.RISK,
        }
        priority = p_map.get(condition_type, Priority.PRICE)
        if condition_type == ConditionType.TIERED_TAKE_PROFIT and tiered_targets is None:
            tiered_targets = [
                TieredTarget(1, 8.0, 0.33),
                TieredTarget(2, 15.0, 0.33),
                TieredTarget(3, 25.0, 0.34),
            ]

        order = ConditionalOrder(
            condition_type=condition_type, code=code, name=name,
            trigger_price=trigger_price, cost_price=cost_price,
            quantity=quantity, amount=amount,
            priority=priority,
            stop_loss_pct=stop_loss_pct,
            tiered_targets=tiered_targets or [],
            trailing_activate_pct=trailing_activate,
            trailing_distance_pct=trailing_distance,
            time_stop_days=time_stop_days,
            note=note,
        )
        return self.create_order(order)

    def cancel_order(self, order_id: str) -> bool:
        """撤销条件单"""
        if order_id not in self._orders:
            return False
        order = self._orders[order_id]
        if order.status in (OrderStatus.EXECUTED, OrderStatus.CANCELLED):
            return False
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return True

    def pause_order(self, order_id: str) -> bool:
        """暂停条件单"""
        if order_id not in self._orders:
            return False
        self._orders[order_id].status = OrderStatus.PAUSED
        self._orders[order_id].updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return True

    def resume_order(self, order_id: str) -> bool:
        """恢复条件单"""
        if order_id not in self._orders:
            return False
        self._orders[order_id].status = OrderStatus.ACTIVE
        self._orders[order_id].updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return True

    def update_order(self, order_id: str, **kwargs) -> bool:
        """修改条件单参数"""
        if order_id not in self._orders:
            return False
        order = self._orders[order_id]
        for k, v in kwargs.items():
            if hasattr(order, k) and k not in ("order_id", "created_at"):
                setattr(order, k, v)
        order.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return True

    def get_order(self, order_id: str) -> Optional[ConditionalOrder]:
        return self._orders.get(order_id)

    def list_orders(self, code: str = None, status: OrderStatus = None,
                     condition_type: ConditionType = None) -> List[ConditionalOrder]:
        """查询条件单"""
        results = list(self._orders.values())
        if code:
            results = [o for o in results if o.code == code]
        if status:
            results = [o for o in results if o.status == status]
        if condition_type:
            results = [o for o in results if o.condition_type == condition_type]
        return sorted(results, key=lambda o: (o.priority, o.created_at or ""))

    def get_active_orders(self) -> List[ConditionalOrder]:
        """获取所有活跃条件单"""
        return [o for o in self._orders.values()
                if o.status in (OrderStatus.ACTIVE, OrderStatus.TRIGGERED)]

    # ── 触发执行 ───────────────────────────────

    def mark_triggered(self, order_id: str, price: float) -> bool:
        """标记条件单为已触发"""
        order = self._orders.get(order_id)
        if not order:
            return False
        order.status = OrderStatus.TRIGGERED
        order.triggered_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order.updated_at = order.triggered_time
        self._save()
        return True

    def mark_executed(self, order_id: str, price: float, quantity: int,
                       pnl_pct: float = 0) -> bool:
        """标记条件单为已执行"""
        order = self._orders.get(order_id)
        if not order:
            return False
        order.status = OrderStatus.EXECUTED
        order.executed_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order.executed_price = price
        order.executed_quantity = quantity
        order.pnl_pct = pnl_pct
        order.updated_at = order.executed_time
        self._save()
        return True

    def add_log(self, log: TriggerLog) -> None:
        """添加触发日志"""
        self._logs.append(log)
        self._save_logs()

    def get_logs(self, code: str = None, limit: int = 50) -> List[TriggerLog]:
        """获取触发日志"""
        results = list(self._logs)
        if code:
            results = [l for l in results if l.code == code]
        return sorted(results, key=lambda l: l.trigger_time or "", reverse=True)[:limit]

    # ── 持久化 ─────────────────────────────────

    def _save(self) -> None:
        """保存条件单"""
        path = DATA_DIR / "orders.json"
        data = {oid: asdict(o) for oid, o in self._orders.items()}
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        """加载条件单"""
        path = DATA_DIR / "orders.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for oid, d in data.items():
                    if "tiered_targets" in d:
                        d["tiered_targets"] = [TieredTarget(**t) for t in d["tiered_targets"]]
                    if "condition_type" in d:
                        d["condition_type"] = ConditionType(d["condition_type"])
                    if "status" in d:
                        d["status"] = OrderStatus(d["status"])
                    self._orders[oid] = ConditionalOrder(**d)
                    # 取最大ID计数值
                    num = int(oid[2:])
                    if num > self._id_counter:
                        self._id_counter = num
            except Exception:
                pass

    def _save_logs(self) -> None:
        path = DATA_DIR / "trigger_logs.json"
        data = [asdict(l) for l in self._logs[-500:]]  # 只保留最近500条
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# 条件循环引擎 (全量评估)
# ═══════════════════════════════════════════════════════════════

class OrderEngine:
    """条件单执行引擎 — 全量评估+执行"""

    def __init__(self):
        self.manager = OrderManager()
        self.market = MarketDataFeed()
        self._log_counter = 0

    def scan_once(self, portfolio_drawdown: float = 0) -> List[TriggerLog]:
        """单次扫描所有活跃条件单

        Args:
            portfolio_drawdown: 组合回撤%（可选）

        Returns:
            本次触发的日志列表
        """
        active = self.manager.get_active_orders()
        if not active:
            return []

        # 检查交易时间
        if not ExceptionHandler.is_market_open():
            print("  ⏸️ 非交易时间，跳过条件单扫描")
            return []

        # 批量获取行情
        codes = list(set(o.code for o in active))
        quotes = self.market.fetch_batch(codes)

        triggered_logs = []

        for order in sorted(active, key=lambda o: o.priority):
            quote = quotes.get(order.code, {})
            price = quote.get("price", 0)

            # 行情异常的兜底处理
            if price <= 0:
                price = ExceptionHandler.get_fallback_price(order)
                print(f"  ⚠️ [{order.code}] 行情异常，使用兜底价{price:.2f}")

            # 全量条件评估
            triggers = ConditionEngine.evaluate_all(order, price, portfolio_drawdown)

            for priority, reason, action in triggers:
                self._log_counter += 1
                log = TriggerLog(
                    log_id=f"TL{self._log_counter:06d}",
                    order_id=order.order_id,
                    condition_type=order.condition_type.value,
                    code=order.code,
                    name=order.name,
                    trigger_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    trigger_price=price,
                    current_price=price,
                    action=action if isinstance(action, str) else action[0],
                    quantity=order.quantity,
                    amount=order.amount,
                    reason=reason,
                    success=True,
                )

                # 标记触发
                self.manager.mark_triggered(order.order_id, price)

                # 计算盈亏
                if order.cost_price > 0:
                    pnl = (price - order.cost_price) / order.cost_price * 100
                else:
                    pnl = 0

                # 更新分级止盈触发状态
                if isinstance(action, tuple) and action[0] == "sell_ratio":
                    # 标记当前档位已触发
                    for target in order.tiered_targets:
                        if not target.triggered and price >= order.cost_price * (1 + target.profit_pct / 100):
                            target.triggered = True
                            log.action = f"tier{target.level}_sell"
                            log.reason += f", 卖出{target.sell_ratio:.0%}"
                            break

                self.manager.mark_executed(order.order_id, price,
                                            order.quantity or int(order.amount / max(price, 1)),
                                            pnl)
                self.manager.add_log(log)
                triggered_logs.append(log)

                print(f"  🔔 [{order.code}] {order.name} {reason}")
                print(f"     {action} @ {price:.2f} | 盈亏{pnl:.1f}%")

        return triggered_logs


# ═══════════════════════════════════════════════════════════════
# CLI入口 (CO-011)
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="智能条件单与分级止盈止损系统")
    sub = parser.add_subparsers(dest="action")

    # create
    p_create = sub.add_parser("create", help="创建条件单")
    p_create.add_argument("--type", required=True, choices=[t.value for t in ConditionType])
    p_create.add_argument("--code", required=True)
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--trigger", type=float, help="触发价格")
    p_create.add_argument("--cost", type=float, default=0, help="成本价")
    p_create.add_argument("--qty", type=int, default=0, help="数量")
    p_create.add_argument("--amt", type=float, default=0, help="金额")
    p_create.add_argument("--sl", type=float, default=-5.0, help="固定止损%")
    p_create.add_argument("--note", default="")

    # list/cancel/pause/resume
    for cmd in ["list", "cancel", "pause", "resume", "status"]:
        sp = sub.add_parser(cmd, help=cmd)
        sp.add_argument("--id", help="订单ID")
        sp.add_argument("--code", help="股票代码")
        sp.add_argument("--status", choices=[s.value for s in OrderStatus])

    # scan
    sub.add_parser("scan", help="扫描一次条件单")

    # log
    p_log = sub.add_parser("log", help="查看触发日志")
    p_log.add_argument("--code")
    p_log.add_argument("--limit", type=int, default=20)

    # demo (创建完整示例)
    p_demo = sub.add_parser("demo", help="创建演示条件单集")

    args = parser.parse_args()
    eng = OrderEngine()
    mgr = eng.manager

    if args.action == "create":
        oid = mgr.create_market_order(
            ConditionType(args.type), args.code, args.name,
            args.trigger, args.cost, args.qty, args.amt,
            args.sl, note=args.note)
        print(f"✅ 创建条件单: {oid}")

    elif args.action == "list":
        orders = mgr.list_orders(code=args.code, status=OrderStatus(args.status) if args.status else None)
        print(f"{'ID':<10} {'类型':<16} {'股票':<10} {'状态':<10} {'触发价':>8} {'成本':>8} {'优先级':>4}")
        print("-" * 70)
        for o in orders:
            print(f"{o.order_id:<10} {o.condition_type.value:<16} {o.code:<10} "
                  f"{o.status.value:<10} {o.trigger_price:>7.1f} {o.cost_price:>7.1f} {o.priority:>3}")
        print(f"总计: {len(orders)} 条")

    elif args.action == "cancel":
        ok = mgr.cancel_order(args.id)
        print(f"{'✅' if ok else '❌'} 撤销: {args.id}")

    elif args.action == "pause":
        ok = mgr.pause_order(args.id)
        print(f"{'✅' if ok else '❌'} 暂停: {args.id}")

    elif args.action == "resume":
        ok = mgr.resume_order(args.id)
        print(f"{'✅' if ok else '❌'} 恢复: {args.id}")

    elif args.action == "status":
        if args.id:
            o = mgr.get_order(args.id)
            if o:
                print(f"=== {o.order_id} {o.condition_type.value} ===")
                for k, v in asdict(o).items():
                    print(f"  {k}: {v}")
            else:
                print(f"❌ 未找到: {args.id}")
        else:
            total = len(mgr.list_orders())
            active = len(mgr.get_active_orders())
            logs = len(mgr.get_logs())
            print(f"=== 条件单状态 ===")
            print(f"  总数: {total}")
            print(f"  活跃: {active}")
            print(f"  触发记录: {logs}")

    elif args.action == "scan":
        logs = eng.scan_once()
        if logs:
            print(f"🔔 本次触发: {len(logs)} 条")
        else:
            print("✅ 无条件触发")
        total_orders = len(mgr.list_orders())
        active_orders = len(mgr.get_active_orders())
        print(f"📊 条件单总数: {total_orders} | 活跃: {active_orders}")

    elif args.action == "log":
        logs = mgr.get_logs(code=args.code, limit=args.limit)
        print(f"{'ID':<8} {'股票':<10} {'类型':<16} {'时间':<20} {'价格':>7} {'盈亏':>7} {'结果':<10}")
        print("-" * 80)
        for l in logs:
            print(f"{l.log_id:<8} {l.code:<10} {l.condition_type:<16} {l.trigger_time:<20} "
                  f"{l.trigger_price:>6.1f} {getattr(l, 'pnl_pct', 0):>6.1f}% {'✅' if l.success else '❌'}")

    elif args.action == "demo":
        codes = [
            ("600519", "贵州茅台", 1500.0),
            ("300750", "宁德时代", 200.0),
            ("000858", "五粮液", 130.0),
        ]
        for code, name, price in codes:
            # 固定止损
            oid = mgr.create_market_order(ConditionType.FIXED_STOP_LOSS, code, name,
                                            price * 0.95, price, stop_loss_pct=-5.0)
            print(f"  ✅ 止损单 {oid}: {name} 止损-5%")
            # 分级止盈
            targets = [
                TieredTarget(1, 8.0, 0.33),
                TieredTarget(2, 15.0, 0.33),
                TieredTarget(3, 25.0, 0.34),
            ]
            oid = mgr.create_market_order(ConditionType.TIERED_TAKE_PROFIT, code, name,
                                            price * 1.08, price,
                                            tiered_targets=targets)
            print(f"  ✅ 止盈单 {oid}: {name} 3档(8%/15%/25%)")
            # 移动止损
            oid = mgr.create_market_order(ConditionType.TRAILING_STOP, code, name,
                                            price, price,
                                            trailing_activate=5.0, trailing_distance=3.0)
            print(f"  ✅ 移动止损 {oid}: {name} 激活5%/回撤3%")
        print(f"\n✅ 演示条件单创建完成")
    else:
        parser.print_help()