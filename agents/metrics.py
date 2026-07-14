#!/usr/bin/env python3
"""
指标收集模块 - 跟踪系统运行指标
包括LLM调用次数、token使用量、执行时间、成功/失败率等
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict
from threading import Lock

from safe_file_utils import safe_write_file
from logger import plog

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
METRICS_DIR = PROJECT_ROOT / "data" / "metrics"
METRICS_DIR.mkdir(parents=True, exist_ok=True)


class MetricsCollector:
    """指标收集器 - 线程安全"""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._metrics: Dict[str, Any] = {
            "start_time": datetime.now().isoformat(),
            "llm_calls": 0,
            "llm_errors": 0,
            "total_tokens": 0,
            "agent_runs": defaultdict(int),
            "agent_errors": defaultdict(int),
            "agent_durations": defaultdict(list),
            "pool_operations": defaultdict(int),
            "api_calls": defaultdict(int),
            "success_count": 0,
            "failure_count": 0,
        }
        self._session_start = time.time()

    def record_llm_call(self, agent_name: str, success: bool = True,
                        tokens: int = 0, duration: float = 0):
        """记录LLM调用"""
        with self._lock:
            self._metrics["llm_calls"] += 1
            if not success:
                self._metrics["llm_errors"] += 1
            if tokens > 0:
                self._metrics["total_tokens"] += tokens

    def record_agent_start(self, agent_name: str):
        """记录Agent开始执行"""
        with self._lock:
            self._metrics["agent_runs"][agent_name] += 1

    def record_agent_end(self, agent_name: str, success: bool,
                         duration: float = 0):
        """记录Agent结束执行"""
        with self._lock:
            if success:
                self._metrics["success_count"] += 1
            else:
                self._metrics["failure_count"] += 1
                self._metrics["agent_errors"][agent_name] += 1

            if duration > 0:
                self._metrics["agent_durations"][agent_name].append(duration)

    def record_pool_operation(self, pool_name: str, operation: str):
        """记录池操作"""
        with self._lock:
            key = f"{pool_name}.{operation}"
            self._metrics["pool_operations"][key] += 1

    def record_api_call(self, api_name: str, success: bool = True):
        """记录API调用"""
        with self._lock:
            self._metrics["api_calls"][api_name] += 1

    def get_summary(self) -> Dict[str, Any]:
        """获取指标摘要"""
        with self._lock:
            session_duration = time.time() - self._session_start

            # 计算平均执行时间
            avg_durations = {}
            for agent, durations in self._metrics["agent_durations"].items():
                if durations:
                    avg_durations[agent] = sum(durations) / len(durations)

            # 计算成功率
            total_runs = self._metrics["success_count"] + self._metrics["failure_count"]
            success_rate = (self._metrics["success_count"] / total_runs * 100) if total_runs > 0 else 0

            return {
                "session_duration_seconds": round(session_duration, 2),
                "start_time": self._metrics["start_time"],
                "end_time": datetime.now().isoformat(),
                "llm": {
                    "total_calls": self._metrics["llm_calls"],
                    "errors": self._metrics["llm_errors"],
                    "total_tokens": self._metrics["total_tokens"],
                    "error_rate": f"{self._metrics['llm_errors'] / max(1, self._metrics['llm_calls']) * 100:.1f}%"
                },
                "agents": {
                    "total_runs": sum(self._metrics["agent_runs"].values()),
                    "success_count": self._metrics["success_count"],
                    "failure_count": self._metrics["failure_count"],
                    "success_rate": f"{success_rate:.1f}%",
                    "runs_by_agent": dict(self._metrics["agent_runs"]),
                    "avg_duration_seconds": avg_durations
                },
                "pools": {
                    "total_operations": sum(self._metrics["pool_operations"].values()),
                    "operations": dict(self._metrics["pool_operations"])
                }
            }

    def get_all_metrics(self) -> Dict[str, Any]:
        """获取完整指标数据"""
        with self._lock:
            return dict(self._metrics)

    def save_to_file(self, filename: Optional[str] = None):
        """保存指标到文件"""
        with self._lock:
            if filename is None:
                filename = f"metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            filepath = METRICS_DIR / filename

            data = {
                "summary": self.get_summary(),
                "full_metrics": dict(self._metrics),
                "saved_at": datetime.now().isoformat()
            }

            # 使用 safe_write_file 替代裸 open()
            success = safe_write_file(filepath, json.dumps(data, ensure_ascii=False, indent=2))
            if not success:
                logger.error(f"[Metrics] 保存指标失败: {filepath}")

            return str(filepath)

    def reset(self):
        """重置所有指标"""
        with self._lock:
            self._metrics = {
                "start_time": datetime.now().isoformat(),
                "llm_calls": 0,
                "llm_errors": 0,
                "total_tokens": 0,
                "agent_runs": defaultdict(int),
                "agent_errors": defaultdict(int),
                "agent_durations": defaultdict(list),
                "pool_operations": defaultdict(int),
                "api_calls": defaultdict(int),
                "success_count": 0,
                "failure_count": 0,
            }
            self._session_start = time.time()


class AgentMetricsContext:
    """Agent执行指标的上下文管理器"""

    def __init__(self, metrics: MetricsCollector, agent_name: str):
        self.metrics = metrics
        self.agent_name = agent_name
        self.start_time = None
        self.success = True

    def __enter__(self):
        self.start_time = time.time()
        self.metrics.record_agent_start(self.agent_name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        self.success = exc_type is None
        self.metrics.record_agent_end(self.agent_name, self.success, duration)
        return False  # 不阻止异常传播


class LLMCallMetricsContext:
    """LLM调用指标的上下文管理器"""

    def __init__(self, metrics: MetricsCollector, agent_name: str):
        self.metrics = metrics
        self.agent_name = agent_name
        self.start_time = None
        self.tokens = 0
        self.success = True

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        self.success = exc_type is None
        self.metrics.record_llm_call(
            self.agent_name,
            success=self.success,
            tokens=self.tokens,
            duration=duration
        )
        return False


# 全局指标收集器实例
_metrics = None


def get_metrics() -> MetricsCollector:
    """获取全局指标收集器"""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


# 便捷函数
def record_llm_call(agent_name: str, **kwargs):
    get_metrics().record_llm_call(agent_name, **kwargs)


def record_agent_start(agent_name: str):
    get_metrics().record_agent_start(agent_name)


def record_agent_end(agent_name: str, success: bool, duration: float = 0):
    get_metrics().record_agent_end(agent_name, success, duration)


def get_metrics_summary() -> Dict[str, Any]:
    return get_metrics().get_summary()


if __name__ == "__main__":
    # 测试指标收集
    m = MetricsCollector()

    plog("INFO", "=== 指标收集测试 ===")
    # 模拟Agent执行
    with AgentMetricsContext(m, "TestAgent"):
        time.sleep(0.1)
        plog("INFO", "Agent执行中...")
    # 模拟LLM调用
    with LLMCallMetricsContext(m, "TestAgent") as ctx:
        ctx.tokens = 1000
        plog("INFO", "LLM调用中...")
    # 记录池操作
    m.record_pool_operation("持仓池", "add")
    m.record_pool_operation("快筛候选池", "update")

    # 获取摘要
    summary = m.get_summary()
    plog("INFO", "\n📊 指标摘要:")
    plog("INFO", f"  会话时长: {summary['session_duration_seconds']}秒")
    plog("INFO", f"  LLM调用: {summary['llm']['total_calls']}次")
    plog("INFO", f"  Agent运行: {summary['agents']['total_runs']}次")
    plog("INFO", f"  成功率: {summary['agents']['success_rate']}")
    plog("INFO", f"  池操作: {summary['pools']['total_operations']}次")
    # 保存到文件
    saved_path = m.save_to_file()
    plog("INFO", f"\n✅ 指标已保存到: {saved_path}")