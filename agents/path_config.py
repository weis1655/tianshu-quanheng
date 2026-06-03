#!/usr/bin/env python3
"""
路径配置中心 — 架构解耦核心
统一管理系统中所有文件路径，替代散落在各处的硬编码路径。

使用方式：
    from path_config import PathConfig
    
    pc = PathConfig()
    pc.pool_dir          # 五池管理目录
    pc.pool_file("持仓池")  # 持仓池.json 完整路径
    pc.data_file("决策日志.json")  # data/决策日志.json
    pc.history_file("2026-06-03_审查报告.md")  # data/回顾报告/...
"""

import os
import sys
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── 项目根目录（统一计算方式）─────────────────────────────────────
def get_project_root() -> Path:
    """获取项目根目录（config.yaml 所在目录）"""
    # 方法 1：从当前文件向上查找
    current = Path(__file__).resolve()
    # agents/path_config.py → 项目根目录
    root = current.parent.parent
    if (root / "config.yaml").exists():
        return root
    
    # 方法 2：从环境变量
    env_root = os.environ.get("TIANSHU_ROOT")
    if env_root:
        p = Path(env_root)
        if p.exists() and (p / "config.yaml").exists():
            return p
    
    # 方法 3：从 sys.path 中找
    for sp in sys.path:
        p = Path(sp)
        if (p / "config.yaml").exists():
            return p
    
    # 兜底
    logger.warning(f"[PathConfig] 未找到 config.yaml，使用 {root} 作为根目录")
    return root


class PathConfig:
    """
    路径配置中心
    
    所有路径通过配置中心统一获取，支持：
    1. config.yaml 覆盖
    2. 环境变量覆盖
    3. 运行时动态修改
    """
    
    _instance: Optional["PathConfig"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config_path: Optional[Path] = None):
        if self._initialized:
            return
        self._initialized = True
        
        self.root = get_project_root()
        self._config = self._load_config(config_path)
        self._paths: Dict[str, Path] = {}
        
        # 初始化默认路径
        self._init_default_paths()
    
    def _load_config(self, config_path: Optional[Path]) -> Dict[str, Any]:
        """加载 config.yaml"""
        config_path = config_path or (self.root / "config.yaml")
        if not config_path.exists():
            logger.warning(f"[PathConfig] 配置文件不存在: {config_path}，使用默认路径")
            return {}
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"[PathConfig] 配置文件读取失败: {e}")
            return {}
    
    def _init_default_paths(self):
        """初始化默认路径映射"""
        # 从 config.yaml 读取 paths 配置（如果有）
        paths_config = self._config.get("paths", {})
        
        # 默认路径（可被 config.yaml 覆盖）
        defaults = {
            "pools": paths_config.get("pools", "五池管理"),
            "data": paths_config.get("data", "data"),
            "history": paths_config.get("history", "data/回顾报告"),
            "logs": paths_config.get("logs", "logs"),
            "reports": paths_config.get("reports", "data/报告"),
            "memory": paths_config.get("memory", "data/复盘记录"),
            "tracking": paths_config.get("tracking", "data/闭环追踪"),
            "calendar": paths_config.get("calendar", "data/交易日历"),
        }
        
        # 构建完整路径
        self._paths = {
            "pools": self.root / defaults["pools"],
            "data": self.root / defaults["data"],
            "history": self.root / defaults["history"],
            "logs": self.root / defaults["logs"],
            "reports": self.root / defaults["reports"],
            "memory": self.root / defaults["memory"],
            "tracking": self.root / defaults["tracking"],
            "calendar": self.root / defaults["calendar"],
        }
        
        # 确保目录存在
        for p in self._paths.values():
            p.mkdir(parents=True, exist_ok=True)
    
    # ── 直接属性访问 ──────────────────────────────────────────
    
    @property
    def root(self) -> Path:
        return self._root
    
    @root.setter
    def root(self, value: Path):
        self._root = value
    
    @property
    def pools_dir(self) -> Path:
        """五池管理目录"""
        return self._paths["pools"]
    
    @property
    def data_dir(self) -> Path:
        """数据目录"""
        return self._paths["data"]
    
    @property
    def history_dir(self) -> Path:
        """历史报告目录"""
        return self._paths["history"]
    
    @property
    def logs_dir(self) -> Path:
        """日志目录"""
        return self._paths["logs"]
    
    @property
    def reports_dir(self) -> Path:
        """报告目录"""
        return self._paths["reports"]
    
    @property
    def memory_dir(self) -> Path:
        """复盘记录目录"""
        return self._paths["memory"]
    
    @property
    def tracking_dir(self) -> Path:
        """闭环追踪目录"""
        return self._paths["tracking"]
    
    @property
    def calendar_dir(self) -> Path:
        """交易日历目录"""
        return self._paths["calendar"]
    
    # ── 池文件操作 ────────────────────────────────────────────
    
    POOL_NAMES = [
        "快筛候选池", "重点观察池", "边缘池", "持仓池", "S级操作池"
    ]
    
    def pool_file(self, pool_name: str, suffix: str = ".json") -> Path:
        """
        获取池文件路径
        
        Args:
            pool_name: 池名称（如 "持仓池"）
            suffix: 文件后缀
    
        Returns:
            完整文件路径
        """
        # 支持带后缀的输入
        if not pool_name.endswith(suffix):
            pool_name = pool_name + suffix
        return self._paths["pools"] / pool_name
    
    def get_all_pool_files(self) -> Dict[str, Path]:
        """获取所有池文件路径"""
        result = {}
        for name in self.POOL_NAMES:
            result[name] = self.pool_file(name)
        return result
    
    # ── 通用数据文件 ──────────────────────────────────────────
    
    def data_file(self, filename: str) -> Path:
        """获取 data 目录下文件路径"""
        return self._paths["data"] / filename
    
    def history_file(self, filename: str) -> Path:
        """获取历史报告目录下文件路径"""
        return self._paths["history"] / filename
    
    def log_file(self, filename: str) -> Path:
        """获取日志目录下文件路径"""
        return self._paths["logs"] / filename
    
    def report_file(self, filename: str) -> Path:
        """获取报告目录下文件路径"""
        return self._paths["reports"] / filename
    
    def memory_file(self, filename: str) -> Path:
        """获取复盘记录目录下文件路径"""
        return self._paths["memory"] / filename
    
    def tracking_file(self, filename: str) -> Path:
        """获取闭环追踪目录下文件路径"""
        return self._paths["tracking"] / filename
    
    def calendar_file(self, filename: str) -> Path:
        """获取交易日历目录下文件路径"""
        return self._paths["calendar"] / filename
    
    # ── 动态配置 ──────────────────────────────────────────────
    
    def set_path(self, key: str, path: Path) -> None:
        """动态设置路径"""
        self._paths[key] = path
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[PathConfig] 路径已更新: {key} = {path}")
    
    def reload_config(self, config_path: Optional[Path] = None) -> None:
        """重新加载配置"""
        self._config = self._load_config(config_path)
        self._init_default_paths()
        logger.info("[PathConfig] 配置已重载")


# ── 便捷函数（无需实例化）─────────────────────────────────────────

def get_path_config() -> PathConfig:
    """获取单例 PathConfig"""
    return PathConfig()


def pool_file(pool_name: str) -> Path:
    """便捷函数：获取池文件路径"""
    return PathConfig().pool_file(pool_name)


def data_file(filename: str) -> Path:
    """便捷函数：获取 data 文件路径"""
    return PathConfig().data_file(filename)


def history_file(filename: str) -> Path:
    """便捷函数：获取历史文件路径"""
    return PathConfig().history_file(filename)


# ── 单元测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    pc = PathConfig()
    print(f"=== 路径配置中心测试 ===")
    print(f"项目根目录: {pc.root}")
    print(f"五池目录: {pc.pools_dir}")
    print(f"数据目录: {pc.data_dir}")
    print(f"历史目录: {pc.history_dir}")
    print(f"\n池文件:")
    for name, path in pc.get_all_pool_files().items():
        print(f"  {name}: {path}")
    print(f"\n便捷函数:")
    print(f"  pool_file('持仓池'): {pool_file('持仓池')}")
    print(f"  data_file('test.json'): {data_file('test.json')}")
