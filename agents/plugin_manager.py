#!/usr/bin/env python3
"""
Agent 插件管理器 - 实现 Agent 的热插拔和自动发现
支持从 agents/plugins/ 目录自动发现和加载 Agent
"""

import os
import sys
import importlib
import inspect
from pathlib import Path
from typing import Dict, Type, List, Optional, Callable
from dataclasses import dataclass, field


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
PLUGINS_DIR = PROJECT_ROOT / "agents" / "plugins"
PLUGINS_DIR.mkdir(exist_ok=True)


@dataclass
class AgentPlugin:
    """Agent 插件信息"""
    name: str                    # 插件名称
    class_name: str              # Agent 类名
    class_path: str              # 类路径
    file_path: str               # 文件路径
    description: str = ""        # 插件描述
    version: str = "1.0.0"       # 版本号
    enabled: bool = True         # 是否启用
    cls: Optional[Type] = field(default=None, repr=False)  # 实际类


class PluginRegistry:
    """Agent 插件注册表"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._plugins: Dict[str, AgentPlugin] = {}
        self._discovery_paths = [
            PLUGINS_DIR,
            PROJECT_ROOT / "agents"
        ]
        self._discovered = False

    def register(self, plugin: AgentPlugin):
        """注册插件"""
        self._plugins[plugin.name] = plugin
        print(f"📦 插件已注册: {plugin.name} ({plugin.class_name})")

    def unregister(self, name: str):
        """注销插件"""
        if name in self._plugins:
            del self._plugins[name]
            print(f"📦 插件已注销: {name}")

    def get(self, name: str) -> Optional[AgentPlugin]:
        """获取插件"""
        return self._plugins.get(name)

    def get_class(self, name: str) -> Optional[Type]:
        """获取插件类"""
        plugin = self._plugins.get(name)
        if plugin:
            if plugin.cls is None:
                self._load_plugin_class(plugin)
            return plugin.cls
        return None

    def list_all(self) -> List[AgentPlugin]:
        """列出所有插件"""
        return list(self._plugins.values())

    def list_enabled(self) -> List[AgentPlugin]:
        """列出已启用的插件"""
        return [p for p in self._plugins.values() if p.enabled]

    def enable(self, name: str):
        """启用插件"""
        if name in self._plugins:
            self._plugins[name].enabled = True

    def disable(self, name: str):
        """禁用插件"""
        if name in self._plugins:
            self._plugins[name].enabled = False

    def discover(self, force: bool = False) -> int:
        """
        自动发现并注册 Agent 插件
        使用正则表达式扫描源代码文件来发现 Agent 类

        Args:
            force: 是否强制重新发现（即使已经发现过）

        Returns:
            发现并注册的插件数量
        """
        if self._discovered and not force:
            return len(self._plugins)

        self._plugins.clear()
        count = 0

        # 正则表达式匹配类定义
        import re
        class_pattern = re.compile(r'^class\s+(\w+)\s*\(\s*BaseAgent\s*(?:,\s*\w+)*\)\s*:', re.MULTILINE)

        # 扫描所有 agent 文件
        for agent_file in (PROJECT_ROOT / "agents").glob("*_agent.py"):
            if agent_file.stem.startswith("_"):
                continue

            try:
                content = agent_file.read_text(encoding="utf-8")
                matches = class_pattern.findall(content)

                for class_name in matches:
                    # 跳过 BaseAgent 自身
                    if class_name == "BaseAgent":
                        continue

                    # 尝试导入并获取类
                    try:
                        module_name = agent_file.stem
                        if module_name in sys.modules:
                            module = sys.modules[module_name]
                        else:
                            # 直接导入
                            importlib.import_module(module_name)

                        cls = getattr(sys.modules[module_name], class_name, None)
                        if cls and hasattr(cls, 'run'):
                            plugin = AgentPlugin(
                                name=class_name,
                                class_name=class_name,
                                class_path=f"{module_name}.{class_name}",
                                file_path=str(agent_file),
                                description=inspect.getdoc(cls) or ""
                            )
                            plugin.cls = cls
                            self.register(plugin)
                            count += 1
                    except Exception:
                        # 导入失败，记录但继续
                        pass

            except Exception:
                continue

        self._discovered = True
        print(f"\n🔍 插件发现完成: 共发现 {count} 个 Agent")
        return count

    def _load_plugin_class(self, plugin: AgentPlugin):
        """加载插件类"""
        try:
            parts = plugin.class_path.rsplit(".", 1)
            module_path = parts[0]
            class_name = parts[1]

            if module_path in sys.modules:
                module = sys.modules[module_path]
            else:
                module = importlib.import_module(module_path)

            plugin.cls = getattr(module, class_name)
        except Exception as e:
            print(f"❌ 加载插件 {plugin.name} 失败: {e}")

    def create_instance(self, name: str, **kwargs) -> Optional[any]:
        """创建插件实例"""
        cls = self.get_class(name)
        if cls:
            return cls(**kwargs)
        return None


# 全局注册表实例
_registry = None


def get_registry() -> PluginRegistry:
    """获取全局插件注册表"""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def discover_agents() -> int:
    """便捷函数：发现并注册所有 Agent"""
    return get_registry().discover()


def list_agents() -> List[str]:
    """便捷函数：列出所有 Agent 名称"""
    return [p.class_name for p in get_registry().list_enabled()]


def get_agent_class(name: str) -> Optional[Type]:
    """便捷函数：获取 Agent 类"""
    return get_registry().get_class(name)


# Agent 装饰器 - 用于标记和注册 Agent
def agent_plugin(name: str = None, description: str = "", version: str = "1.0.0"):
    """
    Agent 插件装饰器

    用法:
        @agent_plugin(name="MyAgent", description="我的 Agent")
        class MyAgent(BaseAgent):
            ...
    """
    def decorator(cls):
        registry = get_registry()
        plugin = AgentPlugin(
            name=name or cls.__name__,
            class_name=cls.__name__,
            class_path=f"{cls.__module__}.{cls.__name__}",
            file_path=inspect.getfile(cls),
            description=description,
            version=version,
            cls=cls
        )
        registry.register(plugin)
        return cls
    return decorator


if __name__ == "__main__":
    print("=== Agent 插件发现 ===\n")

    registry = get_registry()
    count = registry.discover()

    print("\n📋 已注册的 Agent:")
    for plugin in registry.list_all():
        status = "✅" if plugin.enabled else "❌"
        print(f"  {status} {plugin.name}")
        if plugin.description:
            desc = plugin.description.split("\n")[0][:50]
            print(f"     {desc}...")

    print(f"\n总计: {len(registry.list_all())} 个 Agent ({len(registry.list_enabled())} 已启用)")