#!/usr/bin/env python3
"""
Orchestrator - 规则驱动的任务路由器（重构版）
0次LLM调用，纯规则判断今天该做什么

规则：
- 06:20 → News Agent（新闻联播分析）
- 07:10 → 快筛 + 审查 + 决策（三段串联）
- 盘后任意时间 → 按用户指令执行单段

重构：使用PoolManager集中管理五池操作
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

from pool_manager import PoolManager
from logger import StructuredLogger
from logger import plog


class Orchestrator:
    """规则驱动的任务协调器"""

    def __init__(self):
        self.root = PROJECT_ROOT
        self.data_dir = self.root / "data"
        self.history_dir = self.data_dir / "历史记录"
        self.pool_dir = self.root / "五池管理"
        self.rules_dir = self.root / "规则库"
        self.logger = StructuredLogger("Orchestrator")
        self.pool_manager = PoolManager()

    def decide_phase(self, user_intent: Optional[str] = None) -> str:
        """根据时间或用户意图决定执行哪个阶段"""
        now = datetime.now()
        hour = now.hour
        minute = now.minute

        # 用户指令优先
        if user_intent:
            intent = user_intent.lower()
            if any(k in intent for k in ["快筛", "筛选", "screen"]):
                return "screen"
            if any(k in intent for k in ["审查", "review", "分析"]):
                return "review"
            if any(k in intent for k in ["决策", "decision", "执行"]):
                return "decision"
            if any(k in intent for k in ["复盘", "回顾", "review_today"]):
                return "reflection"
            if any(k in intent for k in ["池", "pool", "状态"]):
                return "pool_status"
            return "full_cycle"

        # 按时间自动触发
        time_val = hour * 60 + minute  # 化为分钟数

        # 06:20 - 新闻分析
        if 6 * 60 + 15 <= time_val <= 6 * 60 + 35:
            return "news_only"

        # 07:10 - 三段闭环
        if 7 * 60 + 5 <= time_val <= 7 * 60 + 20:
            return "full_cycle"

        # 其他时间默认为全流程
        return "full_cycle"

    def get_pools(self) -> dict:
        """读取四池状态（接近决策池已停用，P0-9删除）"""
        pools = {}
        pool_names = ["快筛候选池", "重点观察池", "边缘池", "持仓池", "S级操作池"]
        for name in pool_names:
            data = self.pool_manager.load_pool(name)
            pools[name] = {"stocks": data.get("stocks", [])} if data else {"stocks": []}
        return pools

    def get_today_news(self) -> Optional[str]:
        """读取今日新闻联播分析（如果有）"""
        today = datetime.now().strftime("%Y-%m-%d")
        # 尝试从天枢数据目录读取
        tianshu_news = PROJECT_ROOT / "data" / "历史记录" / f"{today}_宏观前置分析.md"
        if tianshu_news.exists():
            return tianshu_news.read_text(encoding="utf-8")
        return None

    def check_hard_rules(self, stock_code: str, stock_name: str) -> tuple[bool, str]:
        """硬规则前置检查，返回(通过, 原因)"""
        hard_rules = self.rules_dir / "硬规则.md"
        if hard_rules.exists():
            content = hard_rules.read_text(encoding="utf-8")
            # 简单关键词检查
            forbidden = ["ST", "亏损", "退市", "暂停上市"]
            for word in forbidden:
                if word in stock_name or word in stock_code:
                    return False, f"违反硬规则：{word}"

        # T+1 检查（使用PoolManager）
        hold_pool = self.pool_manager.load_pool("持仓池")
        held_stocks = [s.get("股票代码", s.get("代码", "")) for s in hold_pool.get("stocks", [])]
        if stock_code in held_stocks:
            return False, "T+1限制：今日已持仓"

        return True, "通过"

    def build_context(self, phase: str) -> dict:
        """构建执行上下文，供各Agent使用"""
        ctx = {
            "phase": phase,
            "timestamp": datetime.now(),
            "pools": self.get_pools(),
            "today_news": self.get_today_news(),
        }

        # 今日历史记录（直接读文件，不用ctx变量名冲突）
        today = datetime.now().strftime("%Y-%m-%d")
        for report_type, file_key in [("快筛报告", "screen_report"), ("审查报告", "review_report"), ("决策报告", "decision_report")]:
            report_file = self.history_dir / f"{today}_{report_type}.md"
            if report_file.exists():
                ctx[file_key] = report_file.read_text(encoding="utf-8")

        return ctx


if __name__ == "__main__":
    # Orchestrator 供 main.py 调用，不独立运行（run()已删除）
    orch = Orchestrator()
    pools = orch.get_pools()
    plog("INFO", "=== 当前池状态 ===")
    for name, data in pools.items():
        plog("INFO", f"{name}: {len(data.get('stocks', []))} 只")