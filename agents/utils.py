#!/usr/bin/env python3
"""
共享工具模块 - 天枢权衡通用工具

集中管理：
- call_llm: LLM 调用封装
- 股票池读写
- 日期格式化
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from requests import HTTPError, Timeout, ConnectionError
from logger import plog

# API 配置
OPENCODE_API_URL = "https://opencode.ai/zen/v1/chat/completions"
# 尝试从环境变量读取
OPENCODE_API_KEY = os.environ.get("OPENCODE_API_KEY", "")
if not OPENCODE_API_KEY or OPENCODE_API_KEY == "***":
    # 从配置文件读取
    config_file = Path(__file__).parent.parent / ".env"
    if config_file.exists():
        for line in config_file.read_text().splitlines():
            if line.startswith("OPENCODE_API_KEY"):
                OPENCODE_API_KEY = line.split("=")[1].strip()
                break
DEFAULT_MODEL = "minimax-m2.5-free"


def call_llm(prompt: str, system: str = "", max_tokens: int = 1000, temperature: float = 0.3) -> str:
    """
    调用 OpenCode LLM API
    
    Args:
        prompt: 用户提示
        system: 系统提示
        max_tokens: 最大 token 数
        temperature: 采样温度 (0.3 避免返回 null)
    
    Returns:
        模型输出文本，失败时返回错误信息
    """
    import requests
    
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.9,
    }
    
    headers = {"Authorization": f"Bearer {OPENCODE_API_KEY}", "Content-Type": "application/json"}
    
    # 重试逻辑
    for attempt in range(3):
        try:
            r = requests.post(OPENCODE_API_URL, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content is None:
                content = ""
            return str(content) if content else "[模型返回空内容]"
        except (HTTPError, Timeout, ConnectionError) as e:
            if attempt < 2:
                import time
                time.sleep(2 ** attempt)  # 指数退避
            else:
                return f"[LLM调用失败] {e}"
        except Exception as e:
            return f"[LLM调用失败] {e}"
    
    return "[LLM调用失败] 未知错误"


def load_pool_json(pool_name: str, root: Optional[Path] = None) -> list:
    """
    读取五池 JSON
    
    Args:
        pool_name: 池名称，如 "快筛候选池.json"
        root: 项目根目录
    
    Returns:
        股票列表
    """
    if root is None:
        root = Path(__file__).parent.parent.resolve()
    
    pool_file = root / "五池管理" / pool_name
    if not pool_file.exists():
        return []
    
    try:
        data = json.loads(pool_file.read_text(encoding="utf-8"))
        return data.get("stocks", [])
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_pool_json(pool_name: str, stocks: list, root: Optional[Path] = None) -> bool:
    """
    保存五池 JSON
    
    Args:
        pool_name: 池名称
        stocks: 股票列表
        root: 项目根目录
    
    Returns:
        是否成功
    """
    if root is None:
        root = Path(__file__).parent.parent.resolve()
    
    pool_file = root / "五池管理" / pool_name
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        pool_file.write_text(
            json.dumps({"stocks": stocks, "updated": datetime.now().strftime("%Y-%m-%d")}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return True
    except Exception:
        return False


def extract_stock_codes(text: str) -> list:
    """
    从文本提取股票代码和名称
    
    Args:
        text: LLM 输出文本
    
    Returns:
        [(代码, 名称), ...] 列表
    """
    # 支持多种格式
    patterns = [
        r"([\u4e00-\u9fa5]{2,6})[（(]?(\d{6})[）)]?",  # 名称(代码)
        r"(\d{6})[^\u4e00-\u9fa5]*([\u4e00-\u9fa5]{2,6})",  # 代码名称
        r"(\d{6})",  # 仅代码
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            if len(matches[0]) == 2:
                # (名称, 代码) 或 (代码, 名称)
                return [(m[1], m[0]) if m[1].isdigit() else m for m in matches]
            else:
                # 仅代码
                return [(c, "") for c in matches]
    
    return []


def get_today_str() -> str:
    """获取今日日期字符串"""
    return datetime.now().strftime("%Y-%m-%d")


def parse_news_date(date_str: str) -> Optional[datetime]:
    """
    解析新闻日期
    
    Args:
        date_str: 如 "2026-04-26" 或 "今天" / "昨天"
    
    Returns:
        datetime 对象，失败返回 None
    """
    date_str = date_str.strip().lower()
    
    if date_str in ["今天", "today"]:
        return datetime.now()
    elif date_str in ["昨天", "yesterday"]:
        return datetime.now() - timedelta(days=1)
    elif date_str in ["前天"]:
        return datetime.now() - timedelta(days=2)
    
    # 尝试解析日期格式
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%m-%d", "%m/%d"]:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    return None


if __name__ == "__main__":
    # 测试
    result = call_llm("1+1=", max_tokens=10)
    plog("INFO", f"call_llm test: {result}")