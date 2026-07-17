#!/usr/bin/env python3
"""
市场工具模块 — 统一管理股票代码前缀、行情前缀等市场相关工具函数

SSOT（单一真理源）：所有市场前缀逻辑统一维护于此，
各模块不再自实现 add_market_prefix / get_market_prefix

用法：
    from market_utils import add_market_prefix, validate_and_prefix_codes, get_market_prefix
"""

from typing import List


def add_market_prefix(code: str) -> str:
    """
    为股票代码添加市场前缀（sh/sz），用于腾讯API

    Args:
        code: 原始股票代码（如601899 或 601899.SH）

    Returns:
        带市场前缀的代码（如sh601899），无效代码返回空字符串
    """
    if not code:
        return ""
    code = code.strip().upper()
    # 移除任何现有的前缀/后缀
    code = code.replace(".SH", "").replace(".SZ", "").replace("SH", "").replace("SZ", "")
    if len(code) == 6 and code.isdigit():
        market = "sh" if code.startswith(("6", "5")) else "sz"
        return f"{market}{code}"
    return ""


def get_market_prefix(code: str) -> str:
    """
    获取股票代码的市场前缀，用于新浪/腾讯API

    Args:
        code: 原始股票代码（如601899）

    Returns:
        'sh' / 'sz' / 'bj' 市场前缀
    """
    code = str(code).strip()
    if code.startswith("6"):
        return "sh"
    elif code.startswith("0") or code.startswith("3"):
        return "sz"
    elif code.startswith("8") or code.startswith("4"):
        return "bj"
    return "sh"


def validate_and_prefix_codes(codes: List[str]) -> List[str]:
    """
    验证股票代码列表并添加市场前缀

    Args:
        codes: 原始股票代码列表

    Returns:
        市场前缀已添加的有效代码列表
    """
    prefixed_codes = []
    for code in codes:
        if code:
            prefixed = add_market_prefix(code)
            if prefixed:
                prefixed_codes.append(prefixed)
    return prefixed_codes