#!/usr/bin/env python3
"""
LLM 截断降级策略 — 鲁棒性增强核心
当输入文本超过 LLM 上下文限制时，自动降级处理：
1. 智能截断（保留关键区域）
2. 分段处理（分块 + 汇总）
3. 降级提示词（简化任务）
"""

import re
import logging
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from logger import plog

logger = logging.getLogger(__name__)


@dataclass
class TruncationResult:
    """截断处理结果"""
    strategy: str          # 使用的策略
    original_length: int   # 原始长度
    processed_length: int  # 处理后长度
    content: str           # 处理后的内容
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── LLM 上下文限制配置 ────────────────────────────────────────────

CONTEXT_LIMITS = {
    "default": 128_000,      # 默认 128K
    "flash": 128_000,        # 快速模型
    "pro": 32_000,           # 专业模型（保守）
    "mini": 8_000,           # 小模型
    "legacy": 4_000,         # 旧模型
}

# 关键区域关键词（截断时优先保留）
KEY_SECTIONS = [
    # 天枢权衡核心区域
    ("决策", ["决策", "操作", "仓位", "止损", "止盈", "信心度"]),
    ("审查", ["综合评分", "风险", "技术面", "基本面", "情绪"]),
    ("快筛", ["股票代码", "股票名称", "涨幅", "驱动", "推荐理由"]),
    ("新闻", ["S级", "A级", "B级", "新闻标题", "影响"]),
    ("行情", ["上证指数", "创业板", "成交额", "北向资金"]),
    ("五池", ["快筛候选池", "重点观察池", "边缘池", "持仓池", "S级操作池"]),
    # 通用关键区域
    ("代码", ["股票代码", "code", "600", "000", "300"]),
    ("数字", ["涨幅", "成交额", "市盈率", "换手率", "量比"]),
]

# 截断策略优先级
TRUNCATION_STRATEGIES = [
    "key_sections",    # 1. 提取关键区域（最优先）
    "head_tail",       # 2. 保留首尾
    "chunk_summarize", # 3. 分块处理
    "simplify_prompt", # 4. 简化任务
]


def estimate_token_count(text: str) -> int:
    """
    估算文本 token 数（中文字符 × 1.5 + 英文字符 × 0.5）
    
    实际 token 数因模型而异，此为近似值
    """
    if not text:
        return 0
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.5)


def extract_key_sections(text: str, max_length: int) -> Tuple[str, List[str]]:
    """
    提取关键区域
    
    Args:
        text: 原始文本
        max_length: 最大长度
    
    Returns:
        (提取的内容, 提取到的区域名称列表)
    """
    extracted = []
    found_sections = []
    
    for section_name, keywords in KEY_SECTIONS:
        for keyword in keywords:
            # 搜索包含关键词的段落
            pattern = re.compile(
                r'(.{0,150}' + re.escape(keyword) + r'.{0,300})',
                re.DOTALL
            )
            matches = pattern.findall(text)
            for m in matches:
                clean = m.strip()
                if clean and clean not in extracted:
                    extracted.append(clean)
                    if section_name not in found_sections:
                        found_sections.append(section_name)
            
            if len('\n\n'.join(extracted)) >= max_length * 0.8:
                break
        
        if len('\n\n'.join(extracted)) >= max_length * 0.8:
            break
    
    result = '\n\n'.join(extracted)
    if len(result) > max_length:
        result = result[:max_length]
    
    return result, found_sections


def head_tail_truncate(text: str, max_length: int, head_ratio: float = 0.4, tail_ratio: float = 0.4) -> str:
    """
    保留首尾的截断策略
    
    Args:
        text: 原始文本
        max_length: 最大长度
        head_ratio: 头部保留比例
        tail_ratio: 尾部保留比例
    """
    if len(text) <= max_length:
        return text
    
    head_len = int(max_length * head_ratio)
    tail_len = int(max_length * tail_ratio)
    
    head = text[:head_len].rsplit('\n', 1)[-1] if '\n' in text[:head_len] else text[:head_len]
    tail = text[-tail_len:].split('\n', 1)[0] if '\n' in text[-tail_len:] else text[-tail_len:]
    
    return f"{head}\n\n[... 中间内容省略 ({len(text) - head_len - tail_len} 字符) ...\n\n{tail}"


def chunk_text(text: str, chunk_size: int = 4000, overlap: int = 200) -> List[str]:
    """
    将文本分块
    
    Args:
        text: 原始文本
        chunk_size: 每块大小
        overlap: 块间重叠
    
    Returns:
        分块列表
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        
        # 尽量在段落边界截断
        if end < len(text):
            last_newline = chunk.rfind('\n')
            if last_newline > chunk_size * 0.5:
                chunk = chunk[:last_newline]
                end = start + last_newline
        
        chunks.append(chunk)
        start = end - overlap
    
    return chunks


def truncate_for_llm(
    text: str,
    max_tokens: int = 128_000,
    model_type: str = "default",
    preserve_structure: bool = True,
) -> TruncationResult:
    """
    智能截断：根据文本长度和模型限制选择最优策略
    
    Args:
        text: 原始文本
        max_tokens: 模型上下文限制（token）
        model_type: 模型类型（用于调整策略）
        preserve_structure: 是否尝试保留文档结构
    
    Returns:
        TruncationResult
    """
    if not text:
        return TruncationResult(
            strategy="empty",
            original_length=0,
            processed_length=0,
            content="",
        )
    
    # 估算当前 token 数
    token_count = estimate_token_count(text)
    char_limit = int(max_tokens * 2)  # 保守估计：1 token ≈ 2 字符（中文）
    
    warnings = []
    
    # 情况 1：无需截断
    if token_count <= max_tokens * 0.9:
        return TruncationResult(
            strategy="no_truncation",
            original_length=len(text),
            processed_length=len(text),
            content=text,
        )
    
    # 情况 2：轻度超限 — 提取关键区域
    if token_count <= max_tokens * 2:
        content, sections = extract_key_sections(text, char_limit)
        if sections and len(content) > 100:
            warnings.append(f"已提取关键区域: {', '.join(sections)}")
            return TruncationResult(
                strategy="key_sections",
                original_length=len(text),
                processed_length=len(content),
                content=content,
                warnings=warnings,
                metadata={"extracted_sections": sections, "token_estimate": token_count},
            )
    
    # 情况 3：中度超限 — 首尾保留
    if token_count <= max_tokens * 4:
        content = head_tail_truncate(text, char_limit)
        warnings.append(f"文本超长，已保留首尾（原始 {len(text)} 字符）")
        return TruncationResult(
            strategy="head_tail",
            original_length=len(text),
            processed_length=len(content),
            content=content,
            warnings=warnings,
            metadata={"token_estimate": token_count},
        )
    
    # 情况 4：严重超限 — 分块处理
    chunks = chunk_text(text, chunk_size=int(char_limit * 0.8))
    warnings.append(f"文本严重超长，已分 {len(chunks)} 块处理")
    
    # 返回第一块 + 元数据
    return TruncationResult(
        strategy="chunk_summarize",
        original_length=len(text),
        processed_length=len(chunks[0]),
        content=chunks[0],
        warnings=warnings,
        metadata={
            "total_chunks": len(chunks),
            "chunk_sizes": [len(c) for c in chunks],
            "token_estimate": token_count,
        },
    )


def get_simplified_prompt(original_prompt: str, truncation_result: TruncationResult) -> str:
    """
    根据截断结果生成简化版提示词
    
    Args:
        original_prompt: 原始提示词
        truncation_result: 截断结果
    
    Returns:
        简化版提示词
    """
    if truncation_result.strategy == "no_truncation":
        return original_prompt
    
    # 添加截断说明
    simplified = f"""注意：输入文本已截断（原始 {truncation_result.original_length} 字符 → 当前 {truncation_result.processed_length} 字符）。

截断策略：{truncation_result.strategy}
"""
    if truncation_result.warnings:
        simplified += f"处理说明：{'；'.join(truncation_result.warnings)}\n\n"
    
    simplified += "---\n\n" + original_prompt
    return simplified


# ── 单元测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    # 生成测试文本
    long_text = """
    # 天枢权衡日报
    
    ## 新闻分析
    今天有多条重要新闻。S级驱动：AI芯片国产化加速。A级驱动：新能源车销量超预期。
    
    ## 快筛结果
    股票代码：600519，股票名称：贵州茅台，涨幅：+2.3%，成交额：50亿。
    股票代码：000823，股票名称：超声电子，涨幅：+5.1%，成交额：8亿。
    
    ## 审查结果
    600519 综合评分：85，风险等级：低风险，技术面：强势，基本面：优秀。
    000823 综合评分：78，风险等级：中风险，技术面：突破，基本面：良好。
    
    ## 决策建议
    600519 操作：建议关注，仓位：15%，止损：1650，止盈：1800，信心度：高。
    000823 操作：建议买入，仓位：25%，止损：14.50，止盈：16.00，信心度：中高。
    
    ## 五池现状
    快筛候选池：12只股票。重点观察池：5只股票。持仓池：3只股票。
    
    """ + "\n".join([f"第{i}行补充内容：这是为了测试截断功能而添加的冗余文本，应该被智能过滤掉。" for i in range(200)])
    
    plog("INFO", f"原始文本: {len(long_text)} 字符, 估算 {estimate_token_count(long_text)} tokens\n")
    result = truncate_for_llm(long_text, max_tokens=4000, model_type="pro")
    plog("INFO", f"策略: {result.strategy}")
    plog("INFO", f"原始: {result.original_length} → 处理后: {result.processed_length}")
    plog("INFO", f"警告: {result.warnings}")
    plog("INFO", f"元数据: {result.metadata}")
    plog("INFO", f"\n处理后内容前200字符: {result.content[:200]}")