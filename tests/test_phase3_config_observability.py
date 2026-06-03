#!/usr/bin/env python3
"""
第三阶段测试 - 配置和可观察性
测试配置加载器、指标收集、健康检查
"""

import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch


class TestConfigLoader:
    """配置加载器测试"""

    def test_config_singleton(self):
        """测试配置单例"""
        from agents.config_loader import ConfigLoader, get_config

        cfg1 = ConfigLoader()
        cfg2 = get_config()
        assert cfg1 is cfg2  # 应该是同一个实例

    def test_get_nested_value(self):
        """测试获取嵌套配置值"""
        from agents.config_loader import get_config

        cfg = get_config()
        assert cfg.get("api.default_model") == "minimax-m2.5-free"
        assert cfg.get("api.llm.temperature") == 0.3
        assert cfg.get("logging.level") == "INFO"

    def test_get_section(self):
        """测试获取配置节"""
        from agents.config_loader import get_config

        cfg = get_config()
        api_section = cfg.get_section("api")
        assert "opencode_url" in api_section
        assert "default_model" in api_section

    def test_default_value(self):
        """测试默认值"""
        from agents.config_loader import get_config

        cfg = get_config()
        assert cfg.get("nonexistent.key", "default") == "default"
        assert cfg.get("api.nonexistent", 42) == 42

    def test_is_feature_enabled(self):
        """测试功能开关"""
        from agents.config_loader import get_config

        cfg = get_config()
        assert cfg.is_feature_enabled("auto_schedule") is True
        assert cfg.is_feature_enabled("nonexistent") is False

    def test_env_var_resolution(self):
        """测试环境变量解析"""
        with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
            from agents.config_loader import ConfigLoader
            # 配置中应该有 ${TEST_VAR:-default} 格式的引用
            cfg = ConfigLoader()
            # 如果配置中使用了环境变量，应该被正确解析
            api_key = cfg.get("api.opencode_key")
            assert api_key is not None


class TestMetricsCollector:
    """指标收集器测试"""

    def test_metrics_singleton(self):
        """测试指标单例"""
        from agents.metrics import MetricsCollector, get_metrics

        m1 = MetricsCollector()
        m2 = get_metrics()
        assert m1 is m2

    def test_record_llm_call(self):
        """测试记录LLM调用"""
        from agents.metrics import MetricsCollector

        m = MetricsCollector()
        m.reset()  # 重置状态
        m.record_llm_call("TestAgent", success=True, tokens=500)
        summary = m.get_summary()
        assert summary["llm"]["total_calls"] >= 1

    def test_record_agent_start_end(self):
        """测试记录Agent开始/结束"""
        from agents.metrics import MetricsCollector

        m = MetricsCollector()
        m.reset()
        m.record_agent_start("TestAgent")
        m.record_agent_end("TestAgent", success=True, duration=1.5)

        summary = m.get_summary()
        assert summary["agents"]["total_runs"] >= 1
        assert summary["agents"]["success_count"] >= 1

    def test_record_pool_operation(self):
        """测试记录池操作"""
        from agents.metrics import MetricsCollector

        m = MetricsCollector()
        m.reset()
        m.record_pool_operation("持仓池", "add")
        m.record_pool_operation("快筛候选池", "update")

        summary = m.get_summary()
        assert summary["pools"]["total_operations"] >= 2

    def test_save_to_file(self):
        """测试保存指标到文件"""
        from agents.metrics import MetricsCollector

        m = MetricsCollector()
        m.reset()
        m.record_agent_start("TestAgent")

        filepath = m.save_to_file()
        assert Path(filepath).exists()

        # 验证文件内容
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        assert "summary" in data
        assert "saved_at" in data

    def test_get_all_metrics(self):
        """测试获取完整指标"""
        from agents.metrics import MetricsCollector

        m = MetricsCollector()
        m.reset()
        m.record_llm_call("TestAgent")

        all_metrics = m.get_all_metrics()
        assert "llm_calls" in all_metrics
        assert isinstance(all_metrics["llm_calls"], int)


class TestHealthChecker:
    """健康检查器测试"""

    def test_check_config(self):
        """测试配置文件检查"""
        from agents.health import HealthChecker

        checker = HealthChecker()
        result = checker.check("配置文件")
        assert result.status == "ok"
        assert result.name == "配置文件"

    def test_check_pool_dir(self):
        """测试池目录检查"""
        from agents.health import HealthChecker

        checker = HealthChecker()
        result = checker.check("池目录")
        assert result.status in ["ok", "warning"]
        assert result.name == "池目录"
        assert "existing_pools" in result.details

    def test_check_holdings_pool(self):
        """测试持仓池检查"""
        from agents.health import HealthChecker

        checker = HealthChecker()
        result = checker.check("持仓池")
        assert result.status in ["ok", "warning"]
        assert "holdings_count" in result.details

    def test_check_all(self):
        """测试全部检查"""
        from agents.health import HealthChecker

        checker = HealthChecker()
        results = checker.check_all()
        assert "配置文件" in results
        assert "池目录" in results
        assert "持仓池" in results

    def test_is_healthy(self):
        """测试快速健康检查"""
        from agents.health import HealthChecker

        checker = HealthChecker()
        is_healthy, message = checker.is_healthy()
        assert isinstance(is_healthy, bool)
        assert isinstance(message, str)

    def test_check_health_function(self):
        """测试check_health函数"""
        from agents.health import check_health

        result = check_health()
        assert result["healthy"] is True
        assert result["status"] == "healthy"
        assert "checks" in result
        assert "summary" in result
        assert "checked_at" in result

    def test_save_health_report(self):
        """测试保存健康报告"""
        from agents.health import save_health_report

        filepath = save_health_report()
        assert Path(filepath).exists()

        # 验证文件内容
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        assert "healthy" in data
        assert data["healthy"] is True


class TestBaseAgentConfigIntegration:
    """BaseAgent配置集成测试"""

    def test_base_agent_uses_config(self):
        """测试BaseAgent使用配置"""
        from agents.base_agent import BaseAgent

        # 测试配置值可以被获取
        url = BaseAgent.get_api_url()
        assert "opencode" in url.lower() or "http" in url.lower()

        model = BaseAgent.get_default_model()
        assert isinstance(model, str)

        temp = BaseAgent.get_llm_temperature()
        assert 0 <= temp <= 2

    def test_base_agent_llm_params_from_config(self):
        """测试BaseAgent从配置读取LLM参数"""
        from agents.base_agent import BaseAgent

        max_tokens = BaseAgent.get_llm_max_tokens()
        assert max_tokens >= 100

        timeout = BaseAgent.get_llm_timeout()
        assert timeout >= 10

        max_retries = BaseAgent.get_llm_max_retries()
        assert max_retries >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])