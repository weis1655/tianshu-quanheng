#!/usr/bin/env python3
"""
配置加载器 - 集中管理配置
支持从config.yaml加载配置，支持环境变量覆盖
"""

import os
import re
import yaml
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime

from safe_file_utils import safe_read_file
from logger import plog

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class ConfigLoader:
    """配置加载器，支持YAML配置和环境变量覆盖"""

    _instance = None
    _config = None

    def __new__(cls, config_path: Optional[Path] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: Optional[Path] = None):
        if self._config is None:
            self._load_config(config_path or DEFAULT_CONFIG_PATH)

    def _load_config(self, config_path: Path):
        """加载配置文件"""
        if not config_path.exists():
            self._config = {}
            return

        content = safe_read_file(config_path, default=None, required=False, log_error=False)
        if content is None:
            self._config = {}
            logger.warning(f"[ConfigLoader] 配置文件读取失败: {config_path}")
            return

        raw_config = yaml.safe_load(content)

        # 处理环境变量引用
        self._config = self._resolve_env_vars(raw_config)

    def _resolve_env_vars(self, obj: Any) -> Any:
        """递归解析环境变量引用 ${VAR:-default}"""
        if isinstance(obj, str):
            # 匹配 ${VAR:-default} 或 ${VAR}
            pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'
            matches = re.findall(pattern, obj)
            for var_name, default in matches:
                value = os.environ.get(var_name, default if default is not None else "")
                obj = obj.replace(f"${{{var_name}:-{default}}}" if default is not None else f"${{{var_name}}}", value)
            return obj
        elif isinstance(obj, dict):
            return {k: self._resolve_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_env_vars(item) for item in obj]
        return obj

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值，支持点号分隔的路径
        例如: config.get("api.opencode_url")
        """
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    def get_section(self, section: str) -> Dict[str, Any]:
        """获取整个配置节"""
        return self._config.get(section, {})

    @property
    def api_config(self) -> Dict[str, Any]:
        """API配置"""
        return self.get_section("api")

    @property
    def pools_config(self) -> Dict[str, Any]:
        """股票池配置"""
        return self.get_section("pools")

    @property
    def screening_config(self) -> Dict[str, Any]:
        """筛选规则配置"""
        return self.get_section("screening")

    @property
    def schedule_config(self) -> Dict[str, Any]:
        """时间调度配置"""
        return self.get_section("schedule")

    @property
    def market_config(self) -> Dict[str, Any]:
        """市场配置"""
        return self.get_section("market")

    @property
    def weights_config(self) -> Dict[str, Any]:
        """权重配置"""
        return self.get_section("weights")

    @property
    def logging_config(self) -> Dict[str, Any]:
        """日志配置"""
        return self.get_section("logging")

    @property
    def paths_config(self) -> Dict[str, Any]:
        """文件路径配置"""
        return self.get_section("paths")

    @property
    def features_config(self) -> Dict[str, Any]:
        """功能开关配置"""
        return self.get_section("features")

    def is_feature_enabled(self, feature: str) -> bool:
        """检查功能是否启用"""
        return self.get(f"features.{feature}", False) is True

    def reload(self):
        """重新加载配置"""
        self._config = None
        self._load_config(DEFAULT_CONFIG_PATH)


# 全局配置实例
_config = None


def get_config() -> ConfigLoader:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config


# 便捷访问函数
def config() -> ConfigLoader:
    """获取配置加载器"""
    return get_config()


if __name__ == "__main__":
    # 测试配置加载
    cfg = get_config()

    plog("INFO", "=== 配置加载测试 ===")
    plog("INFO", f"API URL: {cfg.get('api.opencode_url')}")
    plog("INFO", f"默认模型: {cfg.get('api.default_model')}")
    plog("INFO", f"LLM温度: {cfg.get('api.llm.temperature')}")
    plog("INFO", f"日志级别: {cfg.get('logging.level')}")
    plog("INFO", f"自动调度: {cfg.is_feature_enabled('auto_schedule')}")
    plog("INFO", f"熔断启用: {cfg.is_feature_enabled('circuit_breaker')}")
    # 测试路径配置
    plog("INFO", f"\n池目录: {cfg.get('paths.pool_dir')}")
    plog("INFO", f"历史记录目录: {cfg.get('paths.history_dir')}")
    plog("INFO", "\n✅ 配置加载测试完成")