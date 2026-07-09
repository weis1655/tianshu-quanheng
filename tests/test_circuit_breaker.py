#!/usr/bin/env python3
"""
T-L03 修复验证：熔断器 pytest 测试用例
将 error_handling.py 内联测试转化为标准 pytest 用例
"""

import sys
import os
import pytest
from pathlib import Path
import time

# 添加 agents 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

from error_handling import (
    CircuitBreaker,
    CircuitState,
    CircuitMetrics,
    CircuitOpenError,
    get_circuit_breaker,
    save_circuit_state,
    restore_circuit_state,
)


class TestCircuitBreakerBasic:
    """熔断器基础状态转换测试"""

    def test_initial_state(self):
        """初始状态为CLOSED"""
        breaker = CircuitBreaker("test")
        assert breaker.state == CircuitState.CLOSED
        assert breaker.name == "test"

    def test_success_increments_metrics(self):
        """成功调用递增成功计数"""
        breaker = CircuitBreaker("test")
        breaker.call(lambda: "ok")
        assert breaker.metrics.successful_calls == 1
        assert breaker.metrics.total_calls == 1

    def test_failure_increments_failed(self):
        """失败调用递增失败计数"""
        breaker = CircuitBreaker("test", failure_threshold=3)

        def fail():
            raise ValueError("test fail")

        with pytest.raises(ValueError):
            breaker.call(fail)
        assert breaker.metrics.failed_calls == 1

    def test_open_on_threshold(self):
        """连续失败超过阈值后熔断器打开"""
        breaker = CircuitBreaker("test", failure_threshold=3)

        def fail():
            raise ValueError("fail")

        for _ in range(3):
            with pytest.raises(ValueError):
                breaker.call(fail)

        # 第4次调用应直接抛出CircuitOpenError
        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: "ok")
        assert breaker.state == CircuitState.OPEN

    def test_reset_closes_breaker(self):
        """reset后熔断器回到CLOSED"""
        breaker = CircuitBreaker("test", failure_threshold=2)

        def fail():
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(fail)

        breaker.reset()
        # reset后可正常调用
        result = breaker.call(lambda: "ok")
        assert result == "ok"
        assert breaker.state == CircuitState.CLOSED


class TestCircuitBreakerMetrics:
    """熔断器指标测试"""

    def test_get_status(self):
        """get_status 返回正确格式"""
        breaker = CircuitBreaker("test_service")
        breaker.call(lambda: "ok")
        status = breaker.get_status()
        assert status["name"] == "test_service"
        assert status["total_calls"] == 1
        assert status["failed_calls"] == 0


class TestCircuitBreakerPersistence:
    """熔断器状态持久化测试（T-M04 功能验证）"""

    def test_save_and_restore_state(self, tmp_path):
        """熔断器状态可保存到文件并恢复"""
        breaker = CircuitBreaker("persist_test", failure_threshold=2)

        def fail():
            raise ValueError("fail")

        # 制造失败使熔断器打开
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(fail)

        # 保存状态
        path = tmp_path / "circuit.json"
        save_circuit_state(path, breaker)
        assert path.exists()

        # 恢复状态到新熔断器
        new_breaker = CircuitBreaker("persist_test", failure_threshold=2)
        restore_circuit_state(path, new_breaker)
        assert new_breaker.state == CircuitState.OPEN

    def test_restore_missing_file(self, tmp_path):
        """恢复不存在的文件不报错"""
        breaker = CircuitBreaker("test")
        path = tmp_path / "nonexistent.json"
        restore_circuit_state(path, breaker)  # 不应抛异常


class TestCircuitBreakerRejected:
    """熔断器拒绝调用测试"""

    def test_rejected_calls_counted(self):
        """熔断期间被拒绝的调用会被统计"""
        breaker = CircuitBreaker("test", failure_threshold=1)

        def fail():
            raise ValueError("fail")

        # 触发熔断
        with pytest.raises(ValueError):
            breaker.call(fail)

        # 尝试在熔断期间调用
        with pytest.raises(CircuitOpenError):
            breaker.call(lambda: "ok")

        assert breaker.metrics.rejected_calls == 1


class TestCircuitBreakerHalfOpen:
    """熔断器半开状态测试"""

    def test_half_open_transition(self):
        """超时后熔断器从OPEN转为HALF_OPEN"""
        breaker = CircuitBreaker("test", failure_threshold=2, timeout_seconds=0.1)

        def fail():
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(fail)

        assert breaker.state == CircuitState.OPEN

        # 等待超时
        time.sleep(0.15)

        # 超时后再次调用：先触发半开，然后失败回OPEN
        # 半开状态是瞬时的，_on_failure后回OPEN
        with pytest.raises(ValueError):
            breaker.call(fail)
        assert breaker.state == CircuitState.OPEN

    def test_half_open_success_transitions(self):
        """半开状态成功后关闭熔断器（需连续success_threshold次成功）"""
        breaker = CircuitBreaker("test", failure_threshold=2, timeout_seconds=0.1, success_threshold=1)

        def fail():
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(fail)

        assert breaker.state == CircuitState.OPEN
        time.sleep(0.15)

        # 半开后成功调用（success_threshold=1，1次即关闭）
        result = breaker.call(lambda: "ok")
        assert result == "ok"
        assert breaker.state == CircuitState.CLOSED
