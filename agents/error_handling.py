#!/usr/bin/env python3
"""
高级错误处理模块 - 熔断器模式和重试策略
支持熔断器模式，防止级联故障
"""

import time
import random
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional, Any, Dict
from dataclasses import dataclass
import threading
import functools


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"      # 关闭状态 - 正常调用
    OPEN = "open"         # 打开状态 - 拒绝调用
    HALF_OPEN = "half"    # 半开状态 - 尝试恢复


@dataclass
class CircuitMetrics:
    """熔断器指标"""
    total_calls: int = 0
    failed_calls: int = 0
    successful_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    consecutive_failures: int = 0


class CircuitBreaker:
    """
    熔断器 - 防止级联故障

    原理：
    - 当失败率超过阈值时，打开熔断器
    - 熔断期间拒绝所有请求
    - 过一段时间后，尝试半开状态
    - 如果请求成功则关闭熔断器，否则继续打开
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,      # 失败次数阈值
        success_threshold: int = 3,       # 恢复所需成功次数
        timeout_seconds: int = 60,        # 熔断打开时间
        rejection_threshold: float = 0.5, # 失败率阈值（0.5 = 50%）
        half_open_max_calls: int = 3      # 半开状态最大尝试次数
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds
        self.rejection_threshold = rejection_threshold
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._metrics = CircuitMetrics()
        self._half_open_calls = 0
        self._consecutive_success_in_half = 0  # 半开状态连续成功计数
        self._lock = threading.Lock()
        self._last_state_change = datetime.now()

    @property
    def state(self) -> CircuitState:
        """获取当前状态"""
        with self._lock:
            if self._state == CircuitState.OPEN:
                # 检查是否应该转换到半开
                elapsed = (datetime.now() - self._last_state_change).total_seconds()
                if elapsed >= self.timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
            return self._state

    @property
    def metrics(self) -> CircuitMetrics:
        """获取指标"""
        with self._lock:
            return CircuitMetrics(
                total_calls=self._metrics.total_calls,
                failed_calls=self._metrics.failed_calls,
                successful_calls=self._metrics.successful_calls,
                rejected_calls=self._metrics.rejected_calls,
                last_failure_time=self._metrics.last_failure_time,
                last_success_time=self._metrics.last_success_time,
                consecutive_failures=self._metrics.consecutive_failures
            )

    def is_available(self) -> bool:
        """检查是否接受请求"""
        return self.state != CircuitState.OPEN

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        通过熔断器调用函数

        Args:
            func: 要调用的函数
            *args, **kwargs: 函数参数

        Returns:
            函数返回值

        Raises:
            CircuitOpenError: 熔断器打开时抛出
        """
        if not self.is_available():
            with self._lock:
                self._metrics.rejected_calls += 1
            raise CircuitOpenError(f"熔断器 {self.name} 已打开，拒绝调用")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        """记录成功调用"""
        with self._lock:
            self._metrics.total_calls += 1
            self._metrics.successful_calls += 1
            self._metrics.last_success_time = datetime.now()
            self._metrics.consecutive_failures = 0

            # 如果是半开状态，成功的请求计数
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_success_in_half += 1
                # 如果连续成功次数达到阈值，关闭熔断器
                if self._consecutive_success_in_half >= self.success_threshold:
                    self._set_state(CircuitState.CLOSED)

    def _on_failure(self):
        """记录失败调用"""
        with self._lock:
            self._metrics.total_calls += 1
            self._metrics.failed_calls += 1
            self._metrics.last_failure_time = datetime.now()
            self._metrics.consecutive_failures += 1

            # 检查是否应该打开熔断器
            if self._state == CircuitState.HALF_OPEN:
                # 半开状态下失败，重新打开
                self._consecutive_success_in_half = 0  # 重置连续成功计数
                self._set_state(CircuitState.OPEN)
            elif self._metrics.consecutive_failures >= self.failure_threshold:
                # 检查失败率
                failure_rate = self._metrics.failed_calls / max(1, self._metrics.total_calls)
                if failure_rate >= self.rejection_threshold:
                    self._set_state(CircuitState.OPEN)

    def _set_state(self, new_state: CircuitState):
        """设置状态"""
        if self._state != new_state:
            self._state = new_state
            self._last_state_change = datetime.now()
            state_names = {"closed": "关闭", "open": "打开", "half": "半开"}
            print(f"🔴 熔断器 {self.name}: {state_names.get(self._state.value, self._state.value)}")

    def reset(self):
        """重置熔断器"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._metrics = CircuitMetrics()
            self._half_open_calls = 0
            self._consecutive_success_in_half = 0  # 半开状态连续成功计数
            self._last_state_change = datetime.now()

    def get_status(self) -> Dict[str, Any]:
        """获取状态摘要"""
        metrics = self.metrics
        return {
            "name": self.name,
            "state": self.state.value,
            "total_calls": metrics.total_calls,
            "failed_calls": metrics.failed_calls,
            "success_rate": f"{metrics.successful_calls / max(1, metrics.total_calls) * 100:.1f}%" if metrics.total_calls > 0 else "N/A",
            "rejected_calls": metrics.rejected_calls,
            "consecutive_failures": metrics.consecutive_failures,
            "last_failure": metrics.last_failure_time.isoformat() if metrics.last_failure_time else None
        }


class CircuitOpenError(Exception):
    """熔断器打开异常"""
    pass


def with_circuit_breaker(breaker: CircuitBreaker):
    """
    熔断器装饰器

    用法:
        @with_circuit_breaker(my_breaker)
        def my_function():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return breaker.call(func, *args, **kwargs)
        return wrapper
    return decorator


class RetryStrategy:
    """
    重试策略 - 支持多种重试模式

    支持：
    - 指数退避
    - 固定间隔
    - 抖动（随机延迟）
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        jitter_range: float = 0.5
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.jitter_range = jitter_range

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        执行带重试的函数

        Args:
            func: 要执行的函数
            *args, **kwargs: 函数参数

        Returns:
            函数返回值

        Raises:
            最后一次尝试的异常
        """
        last_exception = None

        for attempt in range(self.max_attempts):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < self.max_attempts - 1:
                    delay = self._calculate_delay(attempt)
                    print(f"⚠️ 尝试 {attempt + 1}/{self.max_attempts} 失败，{delay:.2f}秒后重试: {e}")
                    time.sleep(delay)

        raise last_exception

    def _calculate_delay(self, attempt: int) -> float:
        """计算延迟时间"""
        # 指数退避
        delay = min(self.base_delay * (self.exponential_base ** attempt), self.max_delay)

        # 添加抖动
        if self.jitter:
            jitter_amount = delay * self.jitter_range * random.uniform(-1, 1)
            delay = max(0.1, delay + jitter_amount)

        return delay


def retry_on_failure(max_attempts: int = 3, base_delay: float = 1.0):
    """
    重试装饰器

    用法:
        @retry_on_failure(max_attempts=5, base_delay=2.0)
        def my_function():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            strategy = RetryStrategy(max_attempts=max_attempts, base_delay=base_delay)
            return strategy.execute(func, *args, **kwargs)
        return wrapper
    return decorator


# 全局熔断器管理器
_circuit_breakers: Dict[str, CircuitBreaker] = {}
_circuit_lock = threading.Lock()


def get_circuit_breaker(name: str, **kwargs) -> CircuitBreaker:
    """获取或创建熔断器"""
    with _circuit_lock:
        if name not in _circuit_breakers:
            _circuit_breakers[name] = CircuitBreaker(name, **kwargs)
        return _circuit_breakers[name]


def list_circuit_breakers() -> Dict[str, Dict[str, Any]]:
    """列出所有熔断器状态"""
    with _circuit_lock:
        return {name: cb.get_status() for name, cb in _circuit_breakers.items()}


def check_circuit_breaker(name: str) -> bool:
    """检查熔断器是否允许调用"""
    breaker = get_circuit_breaker(name)
    return breaker.is_available()


def record_success(name: str):
    """记录成功调用"""
    breaker = get_circuit_breaker(name)
    breaker._on_success()


def record_failure(name: str):
    """记录失败调用"""
    breaker = get_circuit_breaker(name)
    breaker._on_failure()


if __name__ == "__main__":
    print("=== 熔断器测试 ===\n")

    # 创建熔断器
    breaker = get_circuit_breaker(
        "api_service",
        failure_threshold=3,
        timeout_seconds=10
    )

    # 模拟调用
    call_count = 0

    def unreliable_api():
        global call_count
        call_count += 1
        if call_count % 3 == 0:
            raise Exception("API 调用失败")
        return "成功"

    # 测试熔断器
    for i in range(10):
        try:
            result = breaker.call(unreliable_api)
            print(f"✅ 调用 {i+1}: {result}")
        except CircuitOpenError as e:
            print(f"🔴 调用 {i+1}: 熔断器打开 - {e}")
        except Exception as e:
            print(f"❌ 调用 {i+1}: 失败 - {e}")

        time.sleep(0.5)

    # 打印状态
    print("\n📊 熔断器状态:")
    for name, status in list_circuit_breakers().items():
        print(f"  {name}: {status['state']} (成功率: {status['success_rate']})")