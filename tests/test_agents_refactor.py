#!/usr/bin/env python3
"""
单元测试 - Agents重构验证
测试所有Agent的基本功能（不调用真实LLM）
"""

import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))


def test_base_agent_stats():
    """测试BaseAgent统计功能"""
    from base_agent import BaseAgent

    class TestAgent(BaseAgent):
        def run(self):
            return {"success": True}

    agent = TestAgent("TestAgent")
    stats = agent.get_stats()

    assert stats["llm_calls"] == 0
    assert stats["llm_errors"] == 0
    assert "start_time" in stats
    assert "runtime_seconds" in stats

    print("✅ test_base_agent_stats")


def test_safe_file_operations():
    """测试安全文件读写"""
    from base_agent import BaseAgent

    class TestAgent(BaseAgent):
        def run(self):
            return {"success": True}

    agent = TestAgent()

    # 创建临时目录
    temp_dir = Path(tempfile.mkdtemp())

    # 测试写JSON
    json_data = {"test": "data", "list": [1, 2, 3]}
    json_file = temp_dir / "test.json"
    result = agent.safe_write_json(json_file, json_data)
    assert result == True
    assert json_file.exists()

    # 测试读JSON
    loaded = agent.safe_read_json(json_file)
    assert loaded == json_data

    # 测试写文本
    text_content = "Hello, World!"
    text_file = temp_dir / "test.txt"
    result = agent.safe_write_text(text_file, text_content)
    assert result == True

    # 测试读文本
    loaded_text = agent.safe_read_text(text_file)
    assert loaded_text == text_content

    # 测试读取不存在的文件
    missing = agent.safe_read_json(temp_dir / "missing.json", {"default": True})
    assert missing == {"default": True}

    # 清理
    shutil.rmtree(temp_dir)
    print("✅ test_safe_file_operations")


def test_news_agent_basic():
    """测试NewsAgent基本功能"""
    from news_agent import NewsAgent

    agent = NewsAgent()
    assert agent.agent_name == "NewsAgent"
    assert agent.history_dir.name == "历史记录"

    stats = agent.get_stats()
    assert stats["llm_calls"] == 0

    print("✅ test_news_agent_basic")


def test_screen_agent_basic():
    """测试ScreenAgent基本功能"""
    from screen_agent import ScreenAgent

    agent = ScreenAgent()
    assert agent.agent_name == "ScreenAgent"
    assert agent.history_dir.name == "历史记录"

    # 粗筛功能需要market_agent，这里只测试初始化
    print(f"   ScreenAgent初始化成功，root: {agent.root}")

    print("✅ test_screen_agent_basic")


def test_review_agent_basic():
    """测试ReviewAgent基本功能"""
    from review_agent import ReviewAgent

    agent = ReviewAgent()
    assert agent.agent_name == "ReviewAgent"
    assert agent.pool_dir.name == "五池管理"

    print(f"   ReviewAgent初始化成功，pool_dir: {agent.pool_dir}")

    print("✅ test_review_agent_basic")


def test_decision_agent_pool_manager():
    """测试DecisionAgent使用PoolManager"""
    from decision_agent import DecisionAgent

    agent = DecisionAgent()
    assert agent.agent_name == "DecisionAgent"
    assert hasattr(agent, "pool_manager")

    # 测试加载池
    pools = agent._load_pools()
    assert isinstance(pools, dict)

    print(f"   DecisionAgent使用PoolManager，池数: {len(pools)}")

    print("✅ test_decision_agent_pool_manager")


def test_orchestrator_pool_manager():
    """测试Orchestrator使用PoolManager"""
    from orchestrator import Orchestrator

    orch = Orchestrator()
    assert hasattr(orch, "pool_manager")

    # 测试获取池
    pools = orch.get_pools()
    assert isinstance(pools, dict)

    # 测试硬规则检查
    pass_check, reason = orch.check_hard_rules("000001", "平安银行")
    assert pass_check == True
    assert reason == "通过"

    # 测试禁止股票
    fail_check, fail_reason = orch.check_hard_rules("000001", "ST平安")
    assert fail_check == False
    assert "ST" in fail_reason

    print("✅ test_orchestrator_pool_manager")


def test_decide_phase_intent():
    """测试用户意图识别"""
    from orchestrator import Orchestrator

    orch = Orchestrator()

    assert orch.decide_phase("快筛") == "screen"
    assert orch.decide_phase("筛选") == "screen"
    assert orch.decide_phase("审查") == "review"
    assert orch.decide_phase("决策") == "decision"
    assert orch.decide_phase("池状态") == "pool_status"
    assert orch.decide_phase("复盘") == "reflection"

    print("✅ test_decide_phase_intent")


def test_build_context():
    """测试上下文构建"""
    from orchestrator import Orchestrator

    orch = Orchestrator()
    ctx = orch.build_context("full_cycle")

    assert ctx["phase"] == "full_cycle"
    assert "pools" in ctx
    assert "timestamp" in ctx

    print("✅ test_build_context")


def test_structured_logger():
    """测试结构化日志"""
    from logger import StructuredLogger, log_execution

    logger = StructuredLogger("TestLogger", level="DEBUG")

    # 测试不同级别
    logger.info("test_info", key="value")
    logger.warning("test_warning", count=123)
    logger.error("test_error", error="test")
    logger.debug("test_debug")

    # 测试便捷方法
    logger.log_agent_start("TestAgent", "test_phase")
    logger.log_agent_end("TestAgent", success=True, duration=1.5, llm_calls=2)
    logger.log_llm_call(prompt_len=100, response_len=200, duration=0.5)
    logger.log_pool_update("持仓池", "add", stock_code="000001", count=1)

    print("✅ test_structured_logger")


def test_log_execution_context_manager():
    """测试log_execution上下文管理器"""
    from logger import log_execution

    with log_execution("test_operation", "Test"):
        import time
        time.sleep(0.05)

    print("✅ test_log_execution_context_manager")


def test_agents_inheritance():
    """测试Agent继承关系"""
    from base_agent import BaseAgent
    from news_agent import NewsAgent
    from screen_agent import ScreenAgent
    from review_agent import ReviewAgent
    from decision_agent import DecisionAgent

    # 验证都继承自BaseAgent
    assert issubclass(NewsAgent, BaseAgent)
    assert issubclass(ScreenAgent, BaseAgent)
    assert issubclass(ReviewAgent, BaseAgent)
    assert issubclass(DecisionAgent, BaseAgent)

    print("✅ test_agents_inheritance")


def test_call_llm_mock():
    """测试模拟LLM调用"""
    from base_agent import BaseAgent

    class TestAgent(BaseAgent):
        def run(self):
            return {"success": True}

    agent = TestAgent("MockTestAgent")

    # 使用mock模拟LLM响应
    mock_response = "这是一条测试响应"

    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": mock_response}}]
        }
        mock_post.return_value.raise_for_status = MagicMock()

        result = agent.call_llm("测试提示", max_tokens=100)

        assert result == mock_response
        assert agent.stats["llm_calls"] == 1

    print("✅ test_call_llm_mock")


def test_feedback_loop_uses_base_agent():
    """测试FeedbackLoop使用BaseAgent"""
    from feedback_loop import FeedbackLoopAgent

    agent = FeedbackLoopAgent()
    assert hasattr(agent, "call_llm")
    assert hasattr(agent, "safe_read_json")
    assert hasattr(agent, "safe_write_json")
    assert hasattr(agent, "get_stats")
    assert hasattr(agent, "pool_manager")

    print("✅ test_feedback_loop_uses_base_agent")


if __name__ == "__main__":
    print("=" * 50)
    print("Agents重构验证测试")
    print("=" * 50)

    test_base_agent_stats()
    test_safe_file_operations()
    test_news_agent_basic()
    test_screen_agent_basic()
    test_review_agent_basic()
    test_decision_agent_pool_manager()
    test_orchestrator_pool_manager()
    test_decide_phase_intent()
    test_build_context()
    test_structured_logger()
    test_log_execution_context_manager()
    test_agents_inheritance()
    test_call_llm_mock()
    test_feedback_loop_uses_base_agent()

    print()
    print("=" * 50)
    print("✅ 所有测试通过")
    print("=" * 50)