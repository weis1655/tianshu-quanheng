#!/usr/bin/env python3
"""
统一日志配置 — 观测性升级核心
提供结构化 JSON 日志 + 传统文本日志双输出，支持：
1. 统一日志格式（含 trace_id、agent_name、stage 等）
2. 多级别输出（DEBUG/INFO/WARNING/ERROR）
3. 结构化日志（便于 ELK/Loki 采集）
4. 日志轮转（按大小 + 按时间）
"""

import os
import sys
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from threading import Lock

# ── 项目路径 ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ── 日志配置 ────────────────────────────────────────────────────

@dataclass
class LogConfig:
    """日志配置"""
    level: str = "INFO"
    log_dir: Path = LOGS_DIR
    max_bytes: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 10
    format_text: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt: str = "%Y-%m-%d %H:%M:%S"
    enable_json: bool = True
    enable_console: bool = True
    enable_file: bool = True


# ── 结构化日志记录 ──────────────────────────────────────────────

@dataclass
class StructuredLogRecord:
    """结构化日志记录"""
    timestamp: str
    level: str
    logger: str
    message: str
    agent: Optional[str] = None
    stage: Optional[str] = None
    trace_id: Optional[str] = None
    stock_code: Optional[str] = None
    pool_name: Optional[str] = None
    duration_ms: Optional[float] = None
    error_type: Optional[str] = None
    error_stack: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器（JSON 输出）"""
    
    def format(self, record: logging.LogRecord) -> str:
        structured = StructuredLogRecord(
            timestamp=datetime.fromtimestamp(record.created).isoformat(),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
        )
        
        # 提取天枢权衡特有字段
        if hasattr(record, 'agent'):
            structured.agent = record.agent
        if hasattr(record, 'stage'):
            structured.stage = record.stage
        if hasattr(record, 'trace_id'):
            structured.trace_id = record.trace_id
        if hasattr(record, 'stock_code'):
            structured.stock_code = record.stock_code
        if hasattr(record, 'pool_name'):
            structured.pool_name = record.pool_name
        if hasattr(record, 'duration_ms'):
            structured.duration_ms = record.duration_ms
        if hasattr(record, 'extra'):
            structured.extra = record.extra
        
        # 错误信息
        if record.exc_info and record.exc_info[0] is not None:
            structured.error_type = record.exc_info[0].__name__
            structured.error_stack = traceback.format_exception(*record.exc_info)
        
        return structured.to_json()


class TextFormatter(logging.Formatter):
    """文本日志格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        # 添加结构化字段到消息中
        extra_parts = []
        for key in ['agent', 'stage', 'trace_id', 'stock_code', 'pool_name', 'duration_ms']:
            if hasattr(record, key):
                val = getattr(record, key)
                if val is not None:
                    extra_parts.append(f"{key}={val}")
        
        msg = record.getMessage()
        if extra_parts:
            msg = f"{msg} [{' | '.join(extra_parts)}]"
        
        record.msg = msg
        return super().format(record)


# ── 日志管理器 ──────────────────────────────────────────────────

class LogManager:
    """
    统一日志管理器（单例）
    
    使用方式：
        from logger import get_logger
        
        logger = get_logger("DecisionAgent")
        logger.info("决策完成", agent="DecisionAgent", stage="run", stock_code="000823")
    """
    
    _instance: Optional["LogManager"] = None
    _lock = Lock()
    
    def __new__(cls, config: Optional[LogConfig] = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self, config: Optional[LogConfig] = None):
        if self._initialized:
            return
        self._initialized = True
        
        self.config = config or LogConfig()
        self._loggers: Dict[str, logging.Logger] = {}
        self._setup_root_logger()
    
    def _setup_root_logger(self):
        """设置根日志器"""
        root = logging.getLogger()
        root.setLevel(getattr(logging, self.config.level.upper()))
        
        # 清除已有 handler
        for h in root.handlers[:]:
            root.removeHandler(h)
        
        # 文本 console handler
        if self.config.enable_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(getattr(logging, self.config.level.upper()))
            console_handler.setFormatter(TextFormatter(self.config.format_text, self.config.datefmt))
            root.addHandler(console_handler)
        
        # 文本 file handler（轮转）
        if self.config.enable_file:
            from logging.handlers import RotatingFileHandler
            
            # 通用日志
            general_log = self.config.log_dir / "tianshu.log"
            file_handler = RotatingFileHandler(
                general_log,
                maxBytes=self.config.max_bytes,
                backupCount=self.config.backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(getattr(logging, self.config.level.upper()))
            file_handler.setFormatter(TextFormatter(self.config.format_text, self.config.datefmt))
            root.addHandler(file_handler)
            
            # JSON 结构化日志
            if self.config.enable_json:
                json_log = self.config.log_dir / "tianshu_structured.jsonl"
                json_handler = RotatingFileHandler(
                    json_log,
                    maxBytes=self.config.max_bytes,
                    backupCount=self.config.backup_count,
                    encoding='utf-8'
                )
                json_handler.setLevel(logging.DEBUG)  # JSON 日志记录所有级别
                json_handler.setFormatter(StructuredFormatter())
                root.addHandler(json_handler)
    
    def get_logger(self, name: str) -> logging.Logger:
        """获取指定名称的 logger"""
        if name not in self._loggers:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)  # 子 logger 默认 DEBUG
            # 子 logger 不重复输出到 root
            logger.propagate = True
            self._loggers[name] = logger
        return self._loggers[name]
    
    def set_level(self, level: str) -> None:
        """动态设置日志级别"""
        logging.getLogger().setLevel(getattr(logging, level.upper()))
        logger.info(f"[LogManager] 日志级别已设置为: {level}")


# ── 便捷函数 ────────────────────────────────────────────────────

_log_manager: Optional[LogManager] = None


def get_logger(name: str = "tianshu") -> logging.Logger:
    """
    获取日志器（便捷函数）
    
    Args:
        name: logger 名称（如 "DecisionAgent", "ScreenAgent"）
    
    Returns:
        logging.Logger 实例
    """
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager()
    return _log_manager.get_logger(name)


def init_logging(config: Optional[LogConfig] = None) -> LogManager:
    """初始化日志系统（显式调用）"""
    global _log_manager
    _log_manager = LogManager(config)
    return _log_manager


# ── 日志上下文管理器 ────────────────────────────────────────────

class LogContext:
    """
    日志上下文管理器
    
    使用方式：
        with LogContext("DecisionAgent", "run", trace_id="abc123"):
            # 此代码块内的所有日志都会带上上下文
            logger.info("开始决策")
    """
    
    def __init__(
        self,
        agent: Optional[str] = None,
        stage: Optional[str] = None,
        trace_id: Optional[str] = None,
        stock_code: Optional[str] = None,
        pool_name: Optional[str] = None,
    ):
        self.agent = agent
        self.stage = stage
        self.trace_id = trace_id
        self.stock_code = stock_code
        self.pool_name = pool_name
        self._old_attrs: Dict[str, Any] = {}
    
    def __enter__(self):
        # 保存当前线程的上下文
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


# ── 性能追踪装饰器 ──────────────────────────────────────────────

def track_duration(logger_name: str = "tianshu"):
    """
    函数执行时长追踪装饰器
    
    使用方式：
        @track_duration("DecisionAgent")
        def run(self):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            logger = get_logger(logger_name)
            start = datetime.now()
            try:
                result = func(*args, **kwargs)
                duration = (datetime.now() - start).total_seconds() * 1000
                logger.info(
                    f"{func.__name__} 执行完成",
                    extra={'duration_ms': round(duration, 2)}
                )
                return result
            except Exception as e:
                duration = (datetime.now() - start).total_seconds() * 1000
                logger.error(
                    f"{func.__name__} 执行失败: {e}",
                    exc_info=True,
                    extra={'duration_ms': round(duration, 2)}
                )
                raise
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator


# ── 单元测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    # 初始化
    config = LogConfig(level="DEBUG", enable_json=True)
    mgr = LogManager(config)
    
    logger = mgr.get_logger("TestAgent")
    
    print("=== 日志系统测试 ===\n")
    
    # 普通日志
    logger.info("这是一条 INFO 日志")
    logger.warning("这是一条 WARNING 日志")
    
    # 带上下文的日志
    logger.info("决策开始", extra={'agent': 'DecisionAgent', 'stage': 'run', 'stock_code': '000823'})
    logger.info("决策完成", extra={
        'agent': 'DecisionAgent',
        'stage': 'run',
        'stock_code': '000823',
        'duration_ms': 1234.5,
    })
    
    # 错误日志
    try:
        1 / 0
    except ZeroDivisionError:
        logger.error("除零错误", exc_info=True)
    
    print(f"\n日志文件:")
    for f in LOGS_DIR.glob("*.log"):
        print(f"  {f} ({f.stat().st_size} bytes)")
    for f in LOGS_DIR.glob("*.jsonl"):
        print(f"  {f} ({f.stat().st_size} bytes)")
