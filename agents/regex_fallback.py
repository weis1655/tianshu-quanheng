#!/usr/bin/env python3
"""
正则提取 fallback 链 — 鲁棒性增强核心
为决策/审查/快筛等提取逻辑提供多策略 fallback，
当首选正则失败时自动降级到备选方案。
"""

import re
import logging
from typing import Optional, List, Dict, Any, Tuple, Callable
from logger import plog

logger = logging.getLogger(__name__)


class RegexFallbackChain:
    """
    正则提取 fallback 链
    
    使用方式：
        chain = RegexFallbackChain()
        chain.add("首选", r"代码.*?(\d{6})")
        chain.add("备选1", r"(\d{6})")
        chain.add("备选2", r"sh(\d{6})|sz(\d{6})")
        
        result = chain.extract(text)  # 返回 (strategy_name, match_dict)
    """
    
    def __init__(self, name: str = "extractor"):
        self.name = name
        self.strategies: List[Tuple[str, str, str]] = []  # (name, pattern, group_names)
    
    def add(self, strategy_name: str, pattern: str, group_names: Optional[List[str]] = None) -> "RegexFallbackChain":
        """
        添加一个提取策略
        
        Args:
            strategy_name: 策略名称（用于日志）
            pattern: 正则表达式
            group_names: 分组名称列表（用于命名分组匹配）
        """
        self.strategies.append((strategy_name, pattern, group_names))
        return self
    
    def extract(self, text: str, extract_all: bool = False) -> Any:
        """
        按优先级依次尝试提取，直到成功
        
        Args:
            text: 待提取文本
            extract_all: 是否返回所有策略的结果（用于调试）
        
        Returns:
            首个成功策略的匹配结果，或 None
        """
        results = []
        for strategy_name, pattern, group_names in self.strategies:
            try:
                match = re.search(pattern, text)
                if match:
                    if extract_all:
                        results.append({
                            "strategy": strategy_name,
                            "success": True,
                            "match": match.group(0)[:200],
                            "groups": match.groups(),
                            "groupdict": match.groupdict() if match.groupdict() else None,
                        })
                    else:
                        if group_names and match.groupdict():
                            return {"strategy": strategy_name, "data": match.groupdict()}
                        elif match.groups():
                            return {"strategy": strategy_name, "data": match.groups()}
                        else:
                            return {"strategy": strategy_name, "data": match.group(0)}
                else:
                    results.append({
                        "strategy": strategy_name,
                        "success": False,
                        "match": None,
                    })
            except re.error as e:
                logger.warning(f"[{self.name}] 正则编译错误 ({strategy_name}): {e}")
                results.append({
                    "strategy": strategy_name,
                    "success": False,
                    "error": str(e),
                })
        
        if extract_all:
            return results
        return None
    
    def extract_list(self, text: str) -> List[Any]:
        """提取所有匹配项（使用首个能匹配的策略）"""
        for strategy_name, pattern, group_names in self.strategies:
            try:
                matches = re.findall(pattern, text)
                if matches:
                    return matches
            except re.error:
                continue
        return []


# ── 预定义提取链 ──────────────────────────────────────────────────

def create_stock_code_extractor() -> RegexFallbackChain:
    """股票代码提取链（6位数字）"""
    return (RegexFallbackChain("股票代码提取")
        .add("精确匹配", r"股票代码[：:]\s*(\d{6})", ["code"])
        .add("带市场前缀", r"(?:sh|sz)(\d{6})", ["code"])
        .add("独立6位", r"(?<![a-zA-Z])(\d{6})(?![a-zA-Z0-9])", ["code"])
        .add("代码字段", r"code[：:]\s*[\"']?(\d{6})[\"']?", ["code"])
        .add("宽松匹配", r"(\d{6})")
    )


def create_stock_name_extractor() -> RegexFallbackChain:
    """股票名称提取链"""
    return (RegexFallbackChain("股票名称提取")
        .add("精确匹配", r"股票名称[：:]\s*[\"']?([^\"'\n]{2,10})[\"']?", ["name"])
        .add("括号匹配", r"(\d{6})\s*[（(]([^）)]{2,10})[）)]", ["code", "name"])
        .add("名称字段", r"name[：:]\s*[\"']?([^\"'\n]{2,10})[\"']?", ["name"])
        .add("紧跟代码", r"(\d{6})\s*[：:]\s*([^,\n]{2,10})", ["code", "name"])
    )


def create_decision_extractor() -> RegexFallbackChain:
    """决策结果提取链（仓位/止损/止盈/操作）"""
    return (RegexFallbackChain("决策提取")
        .add("完整结构", r"操作[：:]\s*([^,\n]+).*?仓位[：:]\s*([^,\n%]+)%", ["action", "position"])
        .add("含仓位", r"仓位[：:]\s*(\d+)%", ["position"])
        .add("含操作", r"操作[：:]\s*([^,\n]+)", ["action"])
        .add("建议字段", r"建议[：:]\s*([^,\n]+)", ["action"])
        .add("宽松", r"(买入|卖出|观望|持有|关注|减仓|加仓)")
    )


def create_review_score_extractor() -> RegexFallbackChain:
    """审查评分提取链"""
    return (RegexFallbackChain("审查评分提取")
        .add("精确匹配", r"综合评分[：:]\s*(\d+)", ["score"])
        .add("分数字段", r"score[：:]\s*(\d+)", ["score"])
        .add("百分比", r"(\d+)\s*%", ["score"])
        .add("宽松", r"评分[：:]\s*(\d+)")
    )


def create_risk_label_extractor() -> RegexFallbackChain:
    """风险标签提取链"""
    return (RegexFallbackChain("风险标签提取")
        .add("精确匹配", r"风险等级[：:]\s*([^,\n]+)", ["risk"])
        .add("风险字段", r"risk[：:]\s*([^,\n]+)", ["risk"])
        .add("标签提取", r"[（(]高[中低]风险[）)]", ["risk"])
        .add("宽松", r"(高风险|中风险|低风险|无风险)")
    )


def create_stop_loss_extractor() -> RegexFallbackChain:
    """止损/止盈提取链"""
    return (RegexFallbackChain("止损止盈提取")
        .add("完整结构", r"止损[：:]\s*([^\s,]+).*?止盈[：:]\s*([^\s,]+)", ["stop_loss", "take_profit"])
        .add("止损", r"止损[：:]\s*([^\s,]+)", ["stop_loss"])
        .add("止盈", r"止盈[：:]\s*([^\s,]+)", ["take_profit"])
        .add("百分比", r"(-?\d+\.?\d*)%", ["price"])
    )


def create_confidence_extractor() -> RegexFallbackChain:
    """信心度提取链"""
    return (RegexFallbackChain("信心度提取")
        .add("精确匹配", r"信心度[：:]\s*([^,\n]+)", ["confidence"])
        .add("信心字段", r"confidence[：:]\s*([^,\n]+)", ["confidence"])
        .add("等级匹配", r"(高|中|低|极高|极低)", ["confidence"])
    )


# ── 文本预处理 fallback ──────────────────────────────────────────

def preprocess_for_extraction(text: str, max_length: int = 8000) -> str:
    """
    文本预处理：截断 + 清理 + 保留关键区域
    
    Args:
        text: 原始文本
        max_length: 最大长度
    
    Returns:
        预处理后的文本
    """
    if not text:
        return ""
    
    # 1. 去除多余空白
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    
    # 2. 如果超长，保留关键区域
    if len(text) > max_length:
        # 优先保留：决策部分、评分部分、代码部分
        key_sections = []
        for keyword in ["决策", "操作", "仓位", "止损", "止盈", "综合评分", "风险", "代码"]:
            pattern = re.compile(r'.{0,200}' + re.escape(keyword) + r'.{0,500}', re.DOTALL)
            matches = pattern.findall(text)
            key_sections.extend(matches)
        
        if key_sections:
            text = '\n\n'.join(set(key_sections))[:max_length]
            logger.info(f"[Preprocess] 文本超长，提取关键区域: {len(text)} 字符")
        else:
            # 无关键区域，保留开头 + 结尾
            mid = max_length // 2
            text = text[:mid] + "\n\n[... 中间内容省略 ...]\n\n" + text[-mid:]
            logger.info(f"[Preprocess] 文本超长，保留首尾: {len(text)} 字符")
    
    return text


# ── 统一提取接口 ──────────────────────────────────────────────────

def extract_stock_info(text: str) -> Dict[str, Any]:
    """
    统一股票信息提取（代码 + 名称）
    
    Returns:
        {"code": str, "name": str, "extraction_strategy": str}
    """
    text = preprocess_for_extraction(text)
    
    # 提取代码
    code_result = create_stock_code_extractor().extract(text)
    code = code_result["data"][0] if code_result and code_result["data"] else None
    
    # 提取名称
    name_result = create_stock_name_extractor().extract(text)
    name = None
    if name_result:
        data = name_result["data"]
        if isinstance(data, tuple):
            name = data[1] if len(data) > 1 else data[0]
        elif isinstance(data, dict):
            name = data.get("name") or data.get("code")
        else:
            name = data
    
    return {
        "code": code,
        "name": name,
        "code_strategy": code_result["strategy"] if code_result else "none",
        "name_strategy": name_result["strategy"] if name_result else "none",
    }


def extract_decision_info(text: str) -> Dict[str, Any]:
    """
    统一决策信息提取（操作 + 仓位 + 止损 + 止盈 + 信心度）
    """
    text = preprocess_for_extraction(text)
    
    result = {
        "action": None,
        "position": None,
        "stop_loss": None,
        "take_profit": None,
        "confidence": None,
        "strategies": {},
    }
    
    # 尝试完整结构提取
    full_result = create_decision_extractor().extract(text)
    if full_result and "action" in str(full_result.get("data", "")):
        data = full_result["data"]
        if isinstance(data, dict):
            result["action"] = data.get("action")
            result["position"] = data.get("position")
        elif isinstance(data, tuple):
            result["action"] = data[0] if len(data) > 0 else None
            result["position"] = data[1] if len(data) > 1 else None
        result["strategies"]["decision"] = full_result["strategy"]
    
    # 单独提取止损止盈
    sl_tp = create_stop_loss_extractor().extract(text)
    if sl_tp:
        data = sl_tp["data"]
        if isinstance(data, dict):
            result["stop_loss"] = data.get("stop_loss")
            result["take_profit"] = data.get("take_profit")
        elif isinstance(data, tuple):
            result["stop_loss"] = data[0] if len(data) > 0 else None
            result["take_profit"] = data[1] if len(data) > 1 else None
        result["strategies"]["stop_loss"] = sl_tp["strategy"]
    
    # 单独提取信心度
    conf = create_confidence_extractor().extract(text)
    if conf:
        data = conf["data"]
        result["confidence"] = data[0] if isinstance(data, tuple) else data
        result["strategies"]["confidence"] = conf["strategy"]
    
    return result


def extract_review_info(text: str) -> Dict[str, Any]:
    """
    统一审查信息提取（评分 + 风险标签）
    """
    text = preprocess_for_extraction(text)
    
    result = {
        "score": None,
        "risk": None,
        "strategies": {},
    }
    
    score_result = create_review_score_extractor().extract(text)
    if score_result:
        data = score_result["data"]
        result["score"] = int(data[0]) if isinstance(data, tuple) else int(data)
        result["strategies"]["score"] = score_result["strategy"]
    
    risk_result = create_risk_label_extractor().extract(text)
    if risk_result:
        data = risk_result["data"]
        result["risk"] = data[0] if isinstance(data, tuple) else data
        result["strategies"]["risk"] = risk_result["strategy"]
    
    return result


# ── 单元测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    plog("INFO", "=== 正则 Fallback 链测试 ===\n")
    # 测试股票代码提取
    test_codes = [
        "股票代码：600519\n股票名称：贵州茅台",
        "sh000001 上证指数",
        "推荐关注 000823 超声电子",
        "code: 603019",
        "600118 中国卫星",
    ]
    
    extractor = create_stock_code_extractor()
    for t in test_codes:
        result = extractor.extract(t)
        plog("INFO", f"输入: {t[:40]}")
        plog("INFO", f"  结果: {result}\n")
    # 测试决策提取
    test_decision = """
    操作：建议买入
    仓位：25%
    止损：14.50
    止盈：16.00
    信心度：高
    """
    result = extract_decision_info(test_decision)
    plog("INFO", f"决策提取: {result}\n")
    # 测试审查提取
    test_review = """
    综合评分：85
    风险等级：低风险
    """
    result = extract_review_info(test_review)
    plog("INFO", f"审查提取: {result}")