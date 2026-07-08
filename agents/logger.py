#!/usr/bin/env python3
"""
Logging Module - 天枢权衡系统统一日志管理
"""

import logging
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import os

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 日志格式
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
JSON_FORMAT = '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}'


class StructuredLogger:
    """结构化日志类，支持JSON格式输出"""
    
    def __init__(self, name: str, level: str = "INFO", json_format: bool = False):
        """
        初始化日志器
        
        Args:
            name: 日志器名称（通常是Agent名称）
            level: 日志级别（DEBUG, INFO, WARNING, ERROR）
            json_format: 是否使用JSON格式
        """
        self.name = name
        self.json_format = json_format
        
        # 创建logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        
        # 清除已有的handlers
        self.logger.handlers.clear()
        
        # 创建formatter
        fmt = JSON_FORMAT if json_format else DEFAULT_FORMAT
        formatter = logging.Formatter(fmt)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # File handler
        log_file = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
    
    def debug(self, message: str, **kwargs):
        """DEBUG级别日志"""
        self._log(logging.DEBUG, message, kwargs)
    
    def info(self, message: str, **kwargs):
        """INFO级别日志"""
        self._log(logging.INFO, message, kwargs)
    
    def warning(self, message: str, **kwargs):
        """WARNING级别日志"""
        self._log(logging.WARNING, message, kwargs)
    
    def error(self, message: str, **kwargs):
        """ERROR级别日志"""
        self._log(logging.ERROR, message, kwargs)
    
    def critical(self, message: str, **kwargs):
        """CRITICAL级别日志"""
        self._log(logging.CRITICAL, message, kwargs)
    
    def _log(self, level: int, message: str, extra: Dict[str, Any]):
        """统一日志输出"""
        if self.json_format and extra:
            # JSON格式：将额外信息添加到消息中
            extra_str = " | " + " ".join([f"{k}={v}" for k, v in extra.items()])
            message = message + extra_str
        
        if level == logging.DEBUG:
            self.logger.debug(message)
        elif level == logging.INFO:
            self.logger.info(message)
        elif level == logging.WARNING:
            self.logger.warning(message)
        elif level == logging.ERROR:
            self.logger.error(message)
        elif level == logging.CRITICAL:
            self.logger.critical(message)
    
    # 便捷方法：记录Agent运行状态
    def log_agent_start(self, agent_name: str, phase: str = ""):
        """记录Agent开始"""
        self.info(f"Agent开始执行", agent=agent_name, phase=phase or "unknown")
    
    def log_agent_end(self, agent_name: str, success: bool, duration: float = 0, 
                   llm_calls: int = 0):
        """记录Agent结束"""
        status = "成功" if success else "失败"
        self.info(f"Agent执行完成", agent=agent_name, status=status, 
                duration_s=duration, llm_calls=llm_calls)
    
    def log_llm_call(self, prompt_len: int, response_len: int, 
                   duration: float = 0, error: str = ""):
        """记录LLM调用"""
        if error:
            self.warning(f"LLM调用失败", error=error, prompt_len=prompt_len)
        else:
            self.debug(f"LLM调用成功", prompt_len=prompt_len, 
                     response_len=response_len, duration_s=duration)
    
    def log_pool_update(self, pool_name: str, action: str, 
                     stock_code: str = "", count: int = 0):
        """记录池更新"""
        self.info(f"池更新", pool=pool_name, action=action, 
                stock=stock_code, count=count)
    
    def log_error(self, error: Exception, context: str = ""):
        """记录错误"""
        self.error(f"异常: {str(error)}", context=context,
                 error_type=type(error).__name__)

    def llm_call(self, operation: str, tokens: int = 0):
        """记录LLM调用"""
        self.info(f"LLM调用", operation=operation, tokens=tokens)

    def pool_operation(self, pool_name: str, action: str, **kwargs):
        """记录池操作"""
        self.info(f"池操作", pool=pool_name, action=action, **kwargs)

    # 上下文管理器：自动记录操作
    def agent_action(self, action: str, **kwargs):
        """自动记录Agent操作的上下文管理器"""
        return log_execution(f"{self.name}.{action}", self.name)


# 全局日志器缓存
_loggers: Dict[str, StructuredLogger] = {}


def get_logger(name: str, level: str = "INFO", json_format: bool = False) -> StructuredLogger:
    """
    获取日志器（单例）
    
    Args:
        name: 日志器名称
        level: 日志级别
        json_format: 是否使用JSON格式
        
    Returns:
        StructuredLogger实例
    """
    global _loggers
    
    key = f"{name}_{level}_{json_format}"
    if key not in _loggers:
        _loggers[key] = StructuredLogger(name, level, json_format)
    
    return _loggers[key]


# 便捷函数：快速获取日志器
def log_info(name: str, message: str, **kwargs):
    """快速INFO日志"""
    get_logger(name).info(message, **kwargs)


def log_error(name: str, message: str, **kwargs):
    """快速ERROR日志"""
    get_logger(name).error(message, **kwargs)


# 上下文管理器：自动记录函数执行时间
class log_execution:
    """自动记录函数执行时间的上下文管理器"""
    
    def __init__(self, name: str, logger_name: str = "default"):
        self.name = name
        self.logger_name = logger_name
        self.logger = get_logger(logger_name)
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        self.logger.info(f"开始执行", operation=self.name)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()
        
        if exc_type is None:
            self.logger.info(f"执行完成", operation=self.name, 
                          duration_s=round(duration, 2), status="success")
        else:
            self.logger.error(f"执行失败", operation=self.name,
                             duration_s=round(duration, 2), 
                             error=str(exc_val),
                             error_type=exc_type.__name__)
        
        return False  # 不阻止异常传播


# ── 简易print替代函数 ──────────────────────────────
# 用法: plog("INFO", "新闻分析完成")   → 输出到stdout + 日志文件
# 级别: ERROR > WARNING > INFO > DEBUG

_LOG_LEVEL_MAP = {
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_ROOT_LOGGER_SETUP = False


def setup_root_logger(level: str = "INFO", log_dir: str = None) -> None:
    """配置根日志器（只执行一次）

    确保 logging.getLogger(__name__) 在所有模块中都能输出到文件+控制台。

    Args:
        level: 日志级别
        log_dir: 日志目录（默认 projects/logs/）
    """
    global _ROOT_LOGGER_SETUP
    if _ROOT_LOGGER_SETUP:
        return
    _ROOT_LOGGER_SETUP = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    fmt = logging.Formatter(DEFAULT_FORMAT)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler
    log_dir_path = Path(log_dir) if log_dir else LOG_DIR
    log_dir_path.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(
        log_dir_path / f"{datetime.now().strftime('%Y-%m-%d')}.log",
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


def plog(level: str, message: str, module: str = "天枢") -> None:
    """print() 替代函数 — 带级别前缀输出到stdout + 日志文件

    格式: [module] message  (保持与现有 print 输出风格兼容)

    Args:
        level: ERROR / WARNING / INFO / DEBUG
        message: 日志内容
        module: 模块名称（显示用）
    """
    # 确保根日志器已初始化
    setup_root_logger()

    level_upper = level.upper()
    log_level = _LOG_LEVEL_MAP.get(level_upper, logging.INFO)
    prefix = {"ERROR": "❌", "WARNING": "⚠️", "INFO": "•", "DEBUG": "🔍"}.get(level_upper, "•")

    # 输出到stdout（保留print的视觉风格，不带时间戳，适合cron/human阅读）
    print(f"{prefix} [{module}] {message}")

    # 输出到日志文件（带完整时间戳和级别）
    logger = logging.getLogger(module)
    logger.log(log_level, "%s %s", prefix, message)


if __name__ == "__main__":
    # 测试日志
    logger = get_logger("TestLogger")
    
    logger.info("测试信息")
    logger.debug("调试信息")
    logger.warning("警告信息")
    logger.error("错误信息")
    
    # 测试上下文管理器
    with log_execution("test_function", "TestLogger"):
        import time
        time.sleep(0.1)
    
    print("\n✅ 日志测试完成!")