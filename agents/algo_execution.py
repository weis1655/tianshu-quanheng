#!/usr/bin/env python3
"""算法交易执行引擎 — TWAP/VWAP/冰山单/主动被动切换

AE-001~008 全功能覆盖：
- TWAP时间加权平均价格拆单
- VWAP成交量加权平均价格拆单  
- 冰山单隐藏真实订单量
- 主动/被动成交策略切换
- 执行监控+滑点统计+偏离度
- 异常处置（行情剧烈/流动性不足/接口异常）
- 历史回放验证
"""
from __future__ import annotations

import json
import math
import time
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = PROJECT_ROOT / "data" / "algo_execution"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 枚举与类型
# ═══════════════════════════════════════════════════════════════

class AlgoType(str, Enum):
    TWAP = "TWAP"           # 时间加权平均价格
    VWAP = "VWAP"           # 成交量加权平均价格
    ICEBERG = "ICEBERG"     # 冰山单
    ADAPTIVE = "ADAPTIVE"   # 自适应切换


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ChildOrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AlgoStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    FALLBACK = "fallback"  # 降级为市价


# ═══════════════════════════════════════════════════════════════
# 数据模型 (AE-001)
# ═══════════════════════════════════════════════════════════════

@dataclass
class AlgoOrder:
    """算法交易主订单"""
    algo_id: str = ""
    algo_type: AlgoType = AlgoType.TWAP
    code: str = ""
    name: str = ""
    side: OrderSide = OrderSide.BUY
    total_quantity: int = 0          # 总数量
    total_amount: float = 0.0        # 总金额
    limit_price: float = 0.0         # 限价（0=市价）
    min_price: float = 0.0           # 最低接受价（止损）
    max_price: float = 0.0           # 最高接受价

    # 算法参数
    duration_minutes: int = 30       # 执行时长（分钟）
    slice_count: int = 10            # 拆单份数
    participation_rate: float = 0.1  # 参与率(0-1) VWAP用
    price_tolerance: float = 0.01    # 价格容忍区间(1%)
    iceberg_visible: int = 0         # 冰山单可见量（0=全量）
    use_passive: bool = True         # 优先被动成交

    # 运行时状态
    status: AlgoStatus = AlgoStatus.CREATED
    child_orders: List[ChildOrder] = field(default_factory=list)
    filled_quantity: int = 0
    filled_amount: float = 0.0
    avg_price: float = 0.0
    vwap: float = 0.0
    slippage_pct: float = 0.0        # 滑点%
    deviation_pct: float = 0.0       # 偏离度%
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    error_msg: str = ""
    created_at: str = ""
    fallback_reason: str = ""


@dataclass
class ChildOrder:
    """子单（拆单后的实际执行单）"""
    child_id: str = ""
    algo_id: str = ""
    seq: int = 0                     # 序号
    side: OrderSide = OrderSide.BUY
    quantity: int = 0
    price: float = 0.0               # 委托价
    status: ChildOrderStatus = ChildOrderStatus.PENDING
    filled_qty: int = 0
    filled_price: float = 0.0
    submitted_at: Optional[str] = None
    filled_at: Optional[str] = None
    slippage: float = 0.0
    error: str = ""


@dataclass
class ExecutionReport:
    """执行报告"""
    algo_id: str
    algo_type: str
    code: str
    name: str
    side: str
    total_qty: int
    total_amount: float
    filled_qty: int
    avg_price: float
    vwap: float
    slippage_pct: float
    duration_seconds: float
    slice_count: int
    status: str
    has_fallback: bool


# ═══════════════════════════════════════════════════════════════
# 行情模拟器（用于回放验证）
# ═══════════════════════════════════════════════════════════════

class PriceSimulator:
    """价格模拟器 — 生成模拟行情路径用于回放验证"""

    @staticmethod
    def generate_path(start_price: float, periods: int,
                       volatility: float = 0.005,
                       trend: float = 0.0,
                       seed: int = 42) -> List[float]:
        """生成模拟价格路径"""
        rng = random.Random(seed)
        prices = [start_price]
        for i in range(periods):
            ret = rng.gauss(trend / periods, volatility)
            prices.append(prices[-1] * (1 + ret))
        return prices

    @staticmethod
    def generate_volume_profile(periods: int, seed: int = 42) -> List[float]:
        """生成模拟成交量分布（U型：开盘和收盘量大）"""
        rng = random.Random(seed)
        profile = []
        for i in range(periods):
            t = i / periods
            # U型分布：开盘和收盘成交量高，中间低
            base = 0.5 + 0.5 * abs(math.sin(t * math.pi))
            noise = rng.uniform(0.7, 1.3)
            profile.append(base * noise)
        total = sum(profile)
        return [p / total for p in profile] if total > 0 else [1/periods] * periods


# ═══════════════════════════════════════════════════════════════
# TWAP算法 (AE-002)
# ═══════════════════════════════════════════════════════════════

class TWAPEngine:
    """TWAP — 时间加权平均价格拆单算法

    原理：将大单均匀拆分为N份，每隔固定时间间隔发送一份子单。
    目标：使成交均价接近整个执行期间的时间加权平均价格。
    """

    @staticmethod
    def calculate_slices(order: AlgoOrder) -> List[Dict]:
        """计算TWAP拆单计划

        Returns:
            [{seq, quantity, scheduled_time, price}, ...]
        """
        slices = []
        qty_per_slice = max(1, order.total_quantity // order.slice_count)
        interval = order.duration_minutes * 60 / order.slice_count  # 秒

        for i in range(order.slice_count):
            qty = qty_per_slice
            if i == order.slice_count - 1:
                qty = order.total_quantity - qty_per_slice * (order.slice_count - 1)
            if qty <= 0:
                continue

            slices.append({
                "seq": i + 1,
                "quantity": qty,
                "scheduled_time": i * interval,
                "price": order.limit_price if order.limit_price > 0 else 0,
            })
        return slices

    @staticmethod
    def execute(order: AlgoOrder,
                price_feed: Callable[[], float]) -> AlgoOrder:
        """执行TWAP拆单

        Args:
            order: 算法订单
            price_feed: 行情回调函数，返回当前价格

        Returns:
            执行完成的订单（含子单成交记录）
        """
        order.status = AlgoStatus.RUNNING
        order.start_time = datetime.now().strftime("%H:%M:%S")
        slices = TWAPEngine.calculate_slices(order)
        interval = order.duration_minutes * 60 / order.slice_count

        start_price = price_feed()
        total_filled_qty = 0
        total_filled_amount = 0.0

        for i, sl in enumerate(slices):
            current_price = price_feed()
            if current_price <= 0:
                current_price = start_price

            # 价格容忍检查
            if order.limit_price > 0:
                if order.side == OrderSide.BUY and current_price > order.limit_price * (1 + order.price_tolerance):
                    # 超出容忍区间，跳过本次切片
                    child = ChildOrder(
                        child_id=f"TWAP_{order.algo_id}_{i+1}",
                        algo_id=order.algo_id, seq=i+1,
                        side=order.side, quantity=sl["quantity"],
                        price=current_price, status=ChildOrderStatus.CANCELLED,
                        error="超出价格容忍区间")
                    order.child_orders.append(child)
                    continue

            # 模拟成交（假设全部成交）
            fill_price = current_price * (1 + random.uniform(-0.001, 0.001))
            fill_qty = sl["quantity"]
            fill_amount = fill_price * fill_qty

            child = ChildOrder(
                child_id=f"TWAP_{order.algo_id}_{i+1}",
                algo_id=order.algo_id, seq=i+1,
                side=order.side, quantity=sl["quantity"],
                price=fill_price, status=ChildOrderStatus.FILLED,
                filled_qty=fill_qty, filled_price=fill_price,
                submitted_at=f"+{i*interval:.0f}s",
                filled_at=f"+{(i+1)*interval:.0f}s",
                slippage=(fill_price - current_price) / current_price * 100,
            )
            order.child_orders.append(child)
            total_filled_qty += fill_qty
            total_filled_amount += fill_amount

            # 间隔等待（模拟中不实际等待）
            if i < len(slices) - 1:
                time.sleep(0.01)  # 模拟微延迟

        # 计算统计
        order.filled_quantity = total_filled_qty
        order.filled_amount = total_filled_amount
        order.avg_price = total_filled_amount / max(total_filled_qty, 1)
        order.vwap = order.avg_price
        order.slippage_pct = (order.avg_price - start_price) / max(start_price, 0.01) * 100
        order.status = AlgoStatus.COMPLETED
        order.end_time = datetime.now().strftime("%H:%M:%S")
        return order

    @staticmethod
    def simulate(order: AlgoOrder,
                 price_path: List[float]) -> AlgoOrder:
        """使用预先生成的价格路径模拟执行（回放验证用）"""
        order.status = AlgoStatus.RUNNING
        order.start_time = datetime.now().strftime("%H:%M:%S")
        slices = TWAPEngine.calculate_slices(order)
        n = len(price_path)
        step = max(1, n // order.slice_count)

        total_filled_qty = 0
        total_filled_amount = 0.0
        start_price = price_path[0]

        for i, sl in enumerate(slices):
            idx = min((i + 1) * step, n - 1)
            price = price_path[idx]

            if order.limit_price > 0:
                if order.side == OrderSide.BUY and price > order.limit_price * (1 + order.price_tolerance):
                    child = ChildOrder(status=ChildOrderStatus.CANCELLED, error="超价格容忍")
                    order.child_orders.append(child)
                    continue

            fill_qty = sl["quantity"]
            fill_amount = price * fill_qty
            child = ChildOrder(
                child_id=f"TWAP_{order.algo_id}_{i+1}",
                algo_id=order.algo_id, seq=i+1, side=order.side,
                quantity=sl["quantity"], price=price,
                status=ChildOrderStatus.FILLED,
                filled_qty=fill_qty, filled_price=price,
                submitted_at=f"t+{i}",
                filled_at=f"t+{i+1}",
                slippage=(price - price_path[idx]) / price_path[idx] * 100,
            )
            order.child_orders.append(child)
            total_filled_qty += fill_qty
            total_filled_amount += fill_amount

        order.filled_quantity = total_filled_qty
        order.filled_amount = total_filled_amount
        order.avg_price = total_filled_amount / max(total_filled_qty, 1)
        twap = sum(price_path) / len(price_path)
        order.slippage_pct = (order.avg_price - twap) / max(twap, 0.01) * 100
        order.status = AlgoStatus.COMPLETED
        order.end_time = datetime.now().strftime("%H:%M:%S")
        return order


# ═══════════════════════════════════════════════════════════════
# VWAP算法 (AE-003)
# ═══════════════════════════════════════════════════════════════

class VWAPEngine:
    """VWAP — 成交量加权平均价格拆单算法

    原理：根据历史成交量分布预测未来成交量，在成交量大的时段多分配订单，
    成交量小的时段少分配，使成交均价接近VWAP。
    """

    @staticmethod
    def calculate_slices(order: AlgoOrder,
                          volume_profile: List[float]) -> List[Dict]:
        """计算VWAP拆单计划

        Args:
            order: 算法订单
            volume_profile: 成交量分布比例（各时段占比，总和=1）

        Returns:
            [{seq, quantity, scheduled_time, price}, ...]
        """
        n = min(len(volume_profile), order.slice_count)
        # 检查volume_profile长度
        vp_len = len(volume_profile)
        total_vol = sum(volume_profile[:n])
        if total_vol <= 0:
            return TWAPEngine.calculate_slices(order)

        slices = []
        allocated = 0
        for i in range(n):
            ratio = volume_profile[i] / total_vol
            qty = max(1, int(order.total_quantity * ratio))
            if i == n - 1:
                qty = order.total_quantity - allocated
            if qty <= 0:
                continue
            allocated += qty
            slices.append({
                "seq": i + 1,
                "quantity": qty,
                "volume_ratio": ratio,
                "price": order.limit_price if order.limit_price > 0 else 0,
            })
        return slices

    @staticmethod
    def simulate(order: AlgoOrder,
                 price_path: List[float],
                 volume_profile: List[float]) -> AlgoOrder:
        """使用模拟行情执行VWAP拆单"""
        order.status = AlgoStatus.RUNNING
        order.start_time = datetime.now().strftime("%H:%M:%S")
        slices = VWAPEngine.calculate_slices(order, volume_profile)
        n = len(price_path)
        step = max(1, n // max(len(slices), 1))

        total_filled_qty = 0
        total_filled_amount = 0.0
        start_price = price_path[0]

        for i, sl in enumerate(slices):
            idx = min((i + 1) * step, n - 1)
            price = price_path[idx]

            if order.limit_price > 0 and order.side == OrderSide.BUY and price > order.limit_price * (1 + order.price_tolerance):
                continue

            fill_qty = sl["quantity"]
            fill_amount = price * fill_qty
            child = ChildOrder(
                child_id=f"VWAP_{order.algo_id}_{i+1}",
                algo_id=order.algo_id, seq=i+1, side=order.side,
                quantity=sl["quantity"], price=price,
                status=ChildOrderStatus.FILLED,
                filled_qty=fill_qty, filled_price=price,
                submitted_at=f"t+{i}", filled_at=f"t+{i+1}",
            )
            order.child_orders.append(child)
            total_filled_qty += fill_qty
            total_filled_amount += fill_amount

        order.filled_quantity = total_filled_qty
        order.filled_amount = total_filled_amount
        order.avg_price = total_filled_amount / max(total_filled_qty, 1)

        # 计算VWAP基准
        vol_sum = sum(volume_profile[:n])
        vwap_n = min(n, len(price_path), len(volume_profile))
        vwap = sum(price_path[i] * volume_profile[i] for i in range(vwap_n)) / max(vol_sum, 0.01) if vol_sum > 0 else order.avg_price
        order.vwap = vwap
        order.slippage_pct = (order.avg_price - vwap) / max(vwap, 0.01) * 100
        order.status = AlgoStatus.COMPLETED
        order.end_time = datetime.now().strftime("%H:%M:%S")
        return order


# ═══════════════════════════════════════════════════════════════
# 冰山单算法 (AE-004)
# ═══════════════════════════════════════════════════════════════

class IcebergEngine:
    """冰山单算法

    原理：将大单拆分为多个小单，每个小单只显示冰山可见量，
    隐藏真实订单总量，避免被市场察觉大额买卖意图。
    """

    @staticmethod
    def calculate_slices(order: AlgoOrder) -> List[Dict]:
        """计算冰山拆单计划"""
        visible = order.iceberg_visible if order.iceberg_visible > 0 else max(1, order.total_quantity // order.slice_count)
        slices = []
        remaining = order.total_quantity
        seq = 0
        while remaining > 0:
            seq += 1
            qty = min(visible, remaining)
            slices.append({"seq": seq, "quantity": qty})
            remaining -= qty
            if seq >= 50:  # 安全限制
                if remaining > 0:
                    slices.append({"seq": seq + 1, "quantity": remaining})
                break
        return slices

    @staticmethod
    def simulate(order: AlgoOrder,
                 price_path: List[float]) -> AlgoOrder:
        """执行冰山单模拟"""
        order.status = AlgoStatus.RUNNING
        order.start_time = datetime.now().strftime("%H:%M:%S")
        slices = IcebergEngine.calculate_slices(order)
        n = len(price_path)
        step = max(1, n // max(len(slices), 1))

        total_filled_qty = 0
        total_filled_amount = 0.0

        for i, sl in enumerate(slices):
            idx = min((i + 1) * step, n - 1)
            price = price_path[idx]
            # 冰山单添加随机延迟，模拟隐藏意图
            delay = random.uniform(0.5, 2.0)
            fill_qty = sl["quantity"]
            fill_amount = price * fill_qty
            child = ChildOrder(
                child_id=f"ICEBERG_{order.algo_id}_{i+1}",
                algo_id=order.algo_id, seq=i+1, side=order.side,
                quantity=sl["quantity"], price=price,
                status=ChildOrderStatus.FILLED,
                filled_qty=fill_qty, filled_price=price,
                submitted_at=f"t+{i}+{delay:.1f}s",
                filled_at=f"t+{i+1}",
            )
            order.child_orders.append(child)
            total_filled_qty += fill_qty
            total_filled_amount += fill_amount

        order.filled_quantity = total_filled_qty
        order.filled_amount = total_filled_amount
        order.avg_price = total_filled_amount / max(total_filled_qty, 1)
        order.status = AlgoStatus.COMPLETED
        order.end_time = datetime.now().strftime("%H:%M:%S")
        return order


# ═══════════════════════════════════════════════════════════════
# 主动/被动策略切换 (AE-005)
# ═══════════════════════════════════════════════════════════════

class AdaptiveEngine:
    """自适应执行引擎 — 主动/被动策略切换

    被动策略：挂限价单等待成交，降低冲击成本但可能无法成交
    主动策略：吃对手盘挂单价，保证成交率但冲击成本较高
    切换条件：根据市场流动性、订单紧急性、价格偏离度动态切换
    """

    @staticmethod
    def decide_strategy(order: AlgoOrder,
                         current_price: float,
                         volume_ratio: float,
                         urgency: str = "normal") -> str:
        """决定使用主动还是被动策略

        Args:
            order: 算法订单
            current_price: 当前价格
            volume_ratio: 当前成交量/历史均值比率
            urgency: low/normal/high

        Returns:
            "passive" 或 "active"
        """
        # 高紧迫 → 主动
        if urgency == "high":
            return "active"

        # 流动性不足 → 被动（避免推高价格）
        if volume_ratio < 0.5:
            return "passive"

        # 价格不利 → 被动等待
        if order.side == OrderSide.BUY and current_price > order.limit_price * 1.01:
            return "passive"

        # 正常情况 → 按配置
        return "passive" if order.use_passive else "active"


# ═══════════════════════════════════════════════════════════════
# 执行监控 (AE-006)
# ═══════════════════════════════════════════════════════════════

class ExecutionMonitor:
    """执行监控 — 实时进度/滑点/偏离度"""

    @staticmethod
    def progress(order: AlgoOrder) -> Dict[str, Any]:
        """当前执行进度"""
        total = order.total_quantity
        filled = order.filled_quantity
        pct = filled / max(total, 1) * 100
        elapsed = 0
        if order.start_time:
            try:
                st = datetime.strptime(order.start_time, "%H:%M:%S")
                elapsed = (datetime.now() - st).total_seconds()
            except ValueError:
                pass
        return {
            "algo_id": order.algo_id,
            "code": order.code,
            "side": order.side.value,
            "total": total,
            "filled": filled,
            "progress_pct": round(pct, 1),
            "avg_price": round(order.avg_price, 2),
            "vwap": round(order.vwap, 2),
            "slippage_pct": round(order.slippage_pct, 3),
            "elapsed_seconds": round(elapsed, 1),
            "child_count": len(order.child_orders),
            "status": order.status.value,
        }

    @staticmethod
    def generate_report(order: AlgoOrder) -> ExecutionReport:
        """生成执行报告"""
        elapsed = 0
        if order.start_time and order.end_time:
            try:
                st = datetime.strptime(order.start_time, "%H:%M:%S")
                et = datetime.strptime(order.end_time, "%H:%M:%S")
                elapsed = (et - st).total_seconds()
            except ValueError:
                pass
        return ExecutionReport(
            algo_id=order.algo_id,
            algo_type=order.algo_type.value,
            code=order.code, name=order.name,
            side=order.side.value,
            total_qty=order.total_quantity,
            total_amount=order.total_amount,
            filled_qty=order.filled_quantity,
            avg_price=round(order.avg_price, 2),
            vwap=round(order.vwap, 2),
            slippage_pct=round(order.slippage_pct, 3),
            duration_seconds=round(elapsed, 1),
            slice_count=len(order.child_orders),
            status=order.status.value,
            has_fallback=order.status == AlgoStatus.FALLBACK,
        )


# ═══════════════════════════════════════════════════════════════
# 异常处置 (AE-007)
# ═══════════════════════════════════════════════════════════════

class ExceptionHandler:
    """异常处置 — 行情剧烈波动/流动性不足/接口异常"""

    @staticmethod
    def check_volatility(price_path: List[float],
                          threshold: float = 0.03) -> Tuple[bool, float]:
        """检查价格波动是否剧烈"""
        if len(price_path) < 2:
            return False, 0
        returns = [abs(price_path[i] / price_path[i-1] - 1) for i in range(1, len(price_path))]
        max_ret = max(returns) if returns else 0
        return max_ret > threshold, max_ret

    @staticmethod
    def check_liquidity(volume_ratio: float,
                         threshold: float = 0.3) -> Tuple[bool, str]:
        """检查流动性"""
        if volume_ratio < threshold:
            return True, f"流动性不足: 当前量/均值量={volume_ratio:.1%}<{threshold:.0%}"
        return False, ""

    @staticmethod
    def handle_exception(order: AlgoOrder,
                          reason: str) -> AlgoOrder:
        """异常处置

        降级策略：
        1. 暂停算法执行
        2. 尝试市价单快速成交剩余量
        3. 标记降级原因
        """
        order.status = AlgoStatus.FALLBACK
        order.fallback_reason = reason
        order.error_msg = f"异常降级: {reason}"
        order.end_time = datetime.now().strftime("%H:%M:%S")

        # 剩余量标记为市价执行
        remaining = order.total_quantity - order.filled_quantity
        if remaining > 0:
            child = ChildOrder(
                child_id=f"FALLBACK_{order.algo_id}",
                algo_id=order.algo_id,
                seq=len(order.child_orders) + 1,
                side=order.side,
                quantity=remaining,
                status=ChildOrderStatus.FAILED,
                error=f"降级市价: {reason[:50]}",
            )
            order.child_orders.append(child)

        return order


# ═══════════════════════════════════════════════════════════════
# 算法执行引擎 (AE-001)
# ═══════════════════════════════════════════════════════════════

class AlgoExecutionEngine:
    """算法交易执行引擎 — 统一入口"""

    def __init__(self):
        self._id_counter = 0
        self._orders: Dict[str, AlgoOrder] = {}
        self._load()

    def create_order(self, algo_type: AlgoType, code: str, name: str,
                      side: OrderSide, total_quantity: int,
                      total_amount: float = 0,
                      limit_price: float = 0,
                      duration_minutes: int = 30,
                      slice_count: int = 10,
                      participation_rate: float = 0.1,
                      iceberg_visible: int = 0,
                      use_passive: bool = True,
                      price_tolerance: float = 0.01) -> str:
        """创建算法订单"""
        self._id_counter += 1
        algo_id = f"AE{self._id_counter:06d}"
        order = AlgoOrder(
            algo_id=algo_id, algo_type=algo_type,
            code=code, name=name, side=side,
            total_quantity=total_quantity, total_amount=total_amount,
            limit_price=limit_price, duration_minutes=duration_minutes,
            slice_count=slice_count, participation_rate=participation_rate,
            iceberg_visible=iceberg_visible, use_passive=use_passive,
            price_tolerance=price_tolerance,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._orders[algo_id] = order
        self._save()
        return algo_id

    def execute(self, algo_id: str,
                 price_path: List[float] = None,
                 volume_profile: List[float] = None) -> AlgoOrder:
        """执行算法订单

        Args:
            algo_id: 算法订单ID
            price_path: 可选的价格路径（用于回放验证）
            volume_profile: 可选的成交量分布（用于VWAP）

        Returns:
            执行完成的订单
        """
        order = self._orders.get(algo_id)
        if not order:
            raise ValueError(f"订单不存在: {algo_id}")

        if price_path is None:
            price_path = PriceSimulator.generate_path(100, order.slice_count)

        if volume_profile is None:
            volume_profile = PriceSimulator.generate_volume_profile(order.slice_count)

        # 异常检测
        is_volatile, max_vol = ExceptionHandler.check_volatility(price_path)
        if is_volatile:
            return ExceptionHandler.handle_exception(
                order, f"行情剧烈波动: 最大涨幅{max_vol:.1%}")

        # 根据算法类型执行
        if order.algo_type == AlgoType.TWAP:
            result = TWAPEngine.simulate(order, price_path)
        elif order.algo_type == AlgoType.VWAP:
            result = VWAPEngine.simulate(order, price_path, volume_profile)
        elif order.algo_type == AlgoType.ICEBERG:
            result = IcebergEngine.simulate(order, price_path)
        else:
            result = TWAPEngine.simulate(order, price_path)

        self._save()
        return result

    def get_order(self, algo_id: str) -> Optional[AlgoOrder]:
        return self._orders.get(algo_id)

    def list_orders(self) -> List[AlgoOrder]:
        return list(self._orders.values())

    def get_report(self, algo_id: str) -> Optional[ExecutionReport]:
        order = self._orders.get(algo_id)
        if not order:
            return None
        return ExecutionMonitor.generate_report(order)

    def compare_results(self, algo_ids: List[str]) -> Dict[str, ExecutionReport]:
        """多算法对比"""
        return {aid: self.get_report(aid) for aid in algo_ids if aid in self._orders}

    # ── 持久化 ──
    def _save(self):
        path = DATA_DIR / "algo_orders.json"
        data = {oid: asdict(o) for oid, o in self._orders.items()}
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self):
        path = DATA_DIR / "algo_orders.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for oid, d in data.items():
                    d["algo_type"] = AlgoType(d["algo_type"])
                    d["side"] = OrderSide(d["side"])
                    d["status"] = AlgoStatus(d.get("status", "created"))
                    if "child_orders" in d:
                        d["child_orders"] = [ChildOrder(**c) for c in d["child_orders"]]
                    self._orders[oid] = AlgoOrder(**d)
                    num = int(oid[2:])
                    if num > self._id_counter:
                        self._id_counter = num
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# 历史回放验证 (AE-008)
# ═══════════════════════════════════════════════════════════════

class BacktestValidator:
    """历史回放验证 — 对比算法执行与一次性下单的滑点差异"""

    @staticmethod
    def run_comparison(algo_type: AlgoType,
                        total_qty: int = 10000,
                        start_price: float = 100.0,
                        periods: int = 60) -> Dict[str, Any]:
        """运行算法 vs 一次性下单对比验证

        Returns:
            {algo_slippage, market_slippage, improvement_pct, ...}
        """
        engine = AlgoExecutionEngine()
        price_path = PriceSimulator.generate_path(start_price, periods, volatility=0.003)
        vol_profile = PriceSimulator.generate_volume_profile(periods)

        # 算法执行
        aid = engine.create_order(algo_type, "000001", "测试", OrderSide.BUY,
                                   total_qty, duration_minutes=30,
                                   slice_count=min(10, periods))
        result = engine.execute(aid, price_path, vol_profile)
        algo_slippage = result.slippage_pct

        # 一次性下单（模拟在中间时刻一次性买入全部）
        mid_idx = periods // 2
        market_price = price_path[mid_idx]
        avg_price_path = sum(price_path) / len(price_path)
        market_slippage = (market_price - avg_price_path) / max(avg_price_path, 0.01) * 100

        improvement = abs(algo_slippage) - abs(market_slippage)

        return {
            "algo_type": algo_type.value,
            "total_qty": total_qty,
            "periods": periods,
            "algo_slippage_pct": round(algo_slippage, 4),
            "market_slippage_pct": round(market_slippage, 4),
            "improvement_pct": round(improvement, 4),
            "algo_avg_price": round(result.avg_price, 2),
            "market_avg_price": round(market_price, 2),
            "algo_vwap": round(result.vwap, 2),
            "slices": len(result.child_orders),
        }

    @staticmethod
    def run_all_comparisons(qty: int = 10000,
                             price: float = 100.0) -> Dict[str, Any]:
        """运行所有算法对比"""
        results = {}
        for at in [AlgoType.TWAP, AlgoType.VWAP, AlgoType.ICEBERG]:
            results[at.value] = BacktestValidator.run_comparison(at, qty, price)
        # 同时对比一次性下单
        results["MARKET"] = {
            "algo_type": "MARKET",
            "slippage_pct": 0,  # 一次性下单无拆单优势
            "note": "一次性下单基准",
        }
        return results

    @staticmethod
    def run_multi_scenario() -> List[Dict]:
        """多场景压力测试"""
        scenarios = [
            ("平稳行情", 100, 0.002, 0.5),
            ("波动行情", 100, 0.008, 0.5),
            ("大涨行情", 105, 0.005, 0.3),
            ("大跌行情", 95, 0.005, 0.3),
            ("低流动性", 100, 0.003, 0.1),
        ]
        all_results = []
        for name, price, vol, vol_ratio in scenarios:
            price_path = PriceSimulator.generate_path(price, 60, volatility=vol)
            vol_prof = [v * vol_ratio for v in PriceSimulator.generate_volume_profile(60)]
            for at in [AlgoType.TWAP, AlgoType.VWAP]:
                engine = AlgoExecutionEngine()
                aid = engine.create_order(at, "000001", "测试", OrderSide.BUY, 10000,
                                           duration_minutes=30, slice_count=10)
                result = engine.execute(aid, price_path, vol_prof)
                all_results.append({
                    "scenario": name,
                    "algo": at.value,
                    "slippage": round(result.slippage_pct, 4),
                    "avg_price": round(result.avg_price, 2),
                    "filled": result.filled_quantity,
                    "status": result.status.value,
                })
        return all_results


# ═══════════════════════════════════════════════════════════════
# CLI入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="算法交易执行引擎")
    sub = parser.add_subparsers(dest="action")

    p_create = sub.add_parser("create", help="创建算法订单")
    p_create.add_argument("--algo", required=True, choices=[t.value for t in AlgoType])
    p_create.add_argument("--code", required=True)
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--side", choices=["buy","sell"], default="buy")
    p_create.add_argument("--qty", type=int, required=True)
    p_create.add_argument("--limit", type=float, default=0)
    p_create.add_argument("--duration", type=int, default=30)
    p_create.add_argument("--slices", type=int, default=10)
    p_create.add_argument("--visible", type=int, default=0, help="冰山单可见量")

    sub.add_parser("list", help="列出所有算法订单")
    sub.add_parser("backtest", help="运行历史回放对比验证")
    sub.add_parser("scenario", help="多场景压力测试")

    p_exec = sub.add_parser("execute", help="执行算法订单")
    p_exec.add_argument("--id", required=True)

    p_rep = sub.add_parser("report", help="查看执行报告")
    p_rep.add_argument("--id", required=True)

    args = parser.parse_args()
    eng = AlgoExecutionEngine()

    if args.action == "create":
        aid = eng.create_order(
            AlgoType(args.algo), args.code, args.name,
            OrderSide(args.side), args.qty,
            limit_price=args.limit,
            duration_minutes=args.duration,
            slice_count=args.slices,
            iceberg_visible=args.visible,
        )
        print(f"✅ 创建算法订单: {aid}")

    elif args.action == "list":
        for o in eng.list_orders():
            print(f"  {o.algo_id} {o.algo_type.value:<8} {o.code:<8} {o.name:<8} "
                  f"{o.side.value:<4} {o.total_quantity:>6}股 {o.status.value:<12} "
                  f"均价{o.avg_price:>8.2f} 滑点{o.slippage_pct:>7.3f}%")

    elif args.action == "execute":
        result = eng.execute(args.id)
        r = ExecutionMonitor.generate_report(result)
        print(f"✅ 执行完成: {r.algo_id}")
        print(f"  类型: {r.algo_type} | 股票: {r.code} {r.name}")
        print(f"  方向: {r.side} | 数量: {r.filled_qty}/{r.total_qty}")
        print(f"  均价: {r.avg_price} | VWAP: {r.vwap}")
        print(f"  滑点: {r.slippage_pct}% | 耗时: {r.duration_seconds}s")
        print(f"  切片: {r.slice_count}笔 | 状态: {r.status}")

    elif args.action == "report":
        r = eng.get_report(args.id)
        if r:
            print(f"=== 执行报告 {r.algo_id} ===")
            for k, v in asdict(r).items():
                print(f"  {k}: {v}")
        else:
            print(f"❌ 未找到: {args.id}")

    elif args.action == "backtest":
        results = BacktestValidator.run_all_comparisons()
        print(f"\n{'='*60}")
        print(f"  算法 vs 一次性下单 滑点对比")
        print(f"{'='*60}")
        print(f"{'算法':<10} {'算法滑点%':>10} {'市价滑点%':>10} {'改善幅度':>10} {'均价':>10}")
        print("-" * 60)
        for k, v in results.items():
            if k == "MARKET":
                print(f"{'市价单':<10} {'---':>10} {'---':>10} {'基准':>10} {'---':>10}")
            else:
                imp = v.get("improvement_pct", 0)
                arrow = "✅" if imp < 0 else "⚠️"
                print(f"{k:<10} {v['algo_slippage_pct']:>10.4f} {v['market_slippage_pct']:>10.4f} "
                      f"{imp:>+9.4f} {arrow} {v['algo_avg_price']:>8.2f}")

    elif args.action == "scenario":
        results = BacktestValidator.run_multi_scenario()
        print(f"\n{'='*60}")
        print(f"  多场景压力测试")
        print(f"{'='*60}")
        print(f"{'场景':<12} {'算法':<8} {'滑点%':>8} {'均价':>10} {'成交':>6} {'状态':<12}")
        print("-" * 60)
        for r in results:
            print(f"{r['scenario']:<12} {r['algo']:<8} {r['slippage']:>8.4f} "
                  f"{r['avg_price']:>10.2f} {r['filled']:>6} {r['status']:<12}")
    else:
        parser.print_help()