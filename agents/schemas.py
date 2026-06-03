#!/usr/bin/env python3
"""
Schemas - 天枢权衡系统统一数据结构定义

包含：
- ScreenOutput / ScreenResult：快筛阶段输入输出结构
- ReviewOutput / ReviewResult：审查阶段输入输出结构
- ExecutionPlan / DecisionResult：决策阶段执行方案结构

设计原则：
- 每个 Result 包含原始输出（供人工/日志回溯）和结构化字段（供程序化处理）
- 统一使用 dataclass，便于序列化/反序列化和类型检查
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime


# ─────────────────────────────────────────
# Screen 阶段
# ─────────────────────────────────────────

@dataclass
class StockCandidate:
    """单只候选股票"""
    code: str          # 股票代码，如 "600118"
    name: str          # 股票名称，如 "中国卫星"
    reason: str        # 入选理由
    driver_level: str   # 驱动级别：S级/A级/B级/C级
    pool: str          # 所属板块/主题
    # P1: 接入实时行情API，使涨幅数据可提取
    current_price: Optional[float] = None   # 实时现价
    change_pct: Optional[float] = None      # 涨跌幅 (%)
    change_amount: Optional[float] = None   # 涨跌额
    turnover_rate: Optional[float] = None   # 换手率 (%)
    volume_ratio: Optional[float] = None    # 量比
    price_time: Optional[str] = None        # 数据时间
    # P1: 技术面评分（修复快筛无评分问题）
    technical_score: Optional[int] = None   # 技术面评分 0-100
    score_reasons: List[str] = field(default_factory=list)   # 评分理由
    risk_warnings: List[str] = field(default_factory=list)   # 风险提示


@dataclass
class ScreenOutput:
    """
    快筛阶段 LLM 的原始文本输出
    （保留格式，供人工确认和日志回溯）
    """
    raw_text: str                          # LLM 原始输出（Markdown 格式）
    timestamp: str                          # 执行时间，ISO 格式


@dataclass
class ScreenResult:
    """
    快筛阶段结构化结果
    （供后续 Agent 直接消费，无需正则提取）
    """
    success: bool
    output: Optional[ScreenOutput] = None  # LLM 原始输出
    candidates: List[StockCandidate] = field(default_factory=list)  # 结构化候选股
    report_file: Optional[str] = None      # 报告文件路径
    error: Optional[str] = None            # 失败原因


# ─────────────────────────────────────────
# Review 阶段
# ─────────────────────────────────────────

@dataclass
class DimensionScore:
    """单个维度评分"""
    dimension: str   # 维度名称
    score: int       # 0-100 分
    note: str = ""   # 维度说明


@dataclass
class StockReview:
    """单只股票的审查结果"""
    code: str                    # 股票代码
    name: str                    # 股票名称
    composite_score: int         # 综合评分 0-100
    confidence: str              # 信心度描述：高/中/低
    driver_level: str            # 驱动级别
    dimensions: List[DimensionScore] = field(default_factory=list)  # 四维评分
    core_logic: str = ""          # 核心逻辑简述
    flow_direction: str = ""      # 流转方向：升级/保留/降级
    target_pool: str = ""        # 目标池名称
    entry_date: str = ""         # 纳入日期
    # 重点观察池额外字段
    recommended_buy: Optional[float] = None   # 推荐买入价
    stop_loss: Optional[float] = None        # 止损触发价
    target_1: Optional[float] = None          # 第一目标价
    target_2: Optional[float] = None          # 第二目标价
    action_advice: str = ""                   # 操作建议：买入/观望/回避
    current_price: Optional[float] = None     # 今日收盘价
    today_change: str = ""                    # 今日涨跌描述


@dataclass
class ReviewOutput:
    """
    审查阶段 LLM 的原始文本输出
    """
    raw_text: str
    timestamp: str


@dataclass
class ReviewResult:
    """
    审查阶段结构化结果
    """
    success: bool
    output: Optional[ReviewOutput] = None
    stocks: List[StockReview] = field(default_factory=list)
    upgrades: List[StockReview] = field(default_factory=list)   # 升级→重点观察池
    demotions: List[StockReview] = field(default_factory=list)   # 降级→边缘池
    report_file: Optional[str] = None
    error: Optional[str] = None


# ─────────────────────────────────────────
# Decision 阶段
# ─────────────────────────────────────────

@dataclass
class ExecutionPlan:
    """
    单只股票的执行方案
    """
    code: str
    name: str
    priority: str              # 主推/备选
    pool_position: str         # 池子位置
    driver: str                # 核心驱动描述
    logic: str                 # 逻辑支撑
    tech_shape: str            # 技术形态
    index_env: str             # 指数环境

    # 仓位与价格
    position_pct: float        # 单笔仓位：X%
    buy_method: str            # 买入方式：追涨/回调买入
    trigger_price: float       # 触发条件价格
    stop_loss: float           # 止损线价格
    stop_loss_pct: float       # 止损百分比

    # 止盈方案
    target_1_price: float      # 第一目标价
    target_1_pct: float        # 第一目标涨幅
    target_1_action: str        # 目标1动作：卖1/2
    target_2_price: float      # 第二目标价
    target_2_pct: float        # 第二目标涨幅
    target_2_action: str       # 目标2动作：清仓

    # 失效条件
    invalid_condition: str     # 失效条件描述
    invalid_price: float       # 失效触发价

    # 不做的情况
    no_go_rules: List[str] = field(default_factory=list)  # 至少3条
    risk_notes: List[str] = field(default_factory=list)  # 风险提示

    # 假设字段（供复盘验证）
    hypothesis: str = ""       # 核心假设
    expected_logic: str = ""   # 预期逻辑链


@dataclass
class DecisionOutput:
    """
    决策阶段 LLM 的原始文本输出
    （P1-2修复：新增 consistency_issues 字段记录审查-决策冲突）
    """
    raw_text: str
    timestamp: str
    consistency_issues: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DecisionResult:
    """
    决策阶段结构化结果
    """
    success: bool
    output: Optional[DecisionOutput] = None
    plans: List[ExecutionPlan] = field(default_factory=list)   # 执行方案列表
    main_tui: List[ExecutionPlan] = field(default_factory=list)  # 【主推】方案
    backup: List[ExecutionPlan] = field(default_factory=list)  # 备选方案
    no_action_reason: str = ""  # 不操作的原因（无合格标的时）
    report_file: Optional[str] = None
    error: Optional[str] = None


# ─────────────────────────────────────────
# 辅助函数：结构化结果 → JSON
# ─────────────────────────────────────────

def screen_result_to_dict(result: ScreenResult) -> Dict[str, Any]:
    """将 ScreenResult 转为字典（供 JSON 写入）"""
    if result.output:
        out_dict = asdict(result.output)
    else:
        out_dict = None
    return {
        "success": result.success,
        "output": out_dict,
        "candidates": [asdict(c) for c in result.candidates],
        "report_file": result.report_file,
        "error": result.error,
    }


def review_result_to_dict(result: ReviewResult) -> Dict[str, Any]:
    """将 ReviewResult 转为字典"""
    def _dim(d: DimensionScore):
        return asdict(d)

    def _sr(s: StockReview):
        d = asdict(s)
        d["dimensions"] = [_dim(d_) for d_ in s.dimensions]
        return d

    return {
        "success": result.success,
        "output": asdict(result.output) if result.output else None,
        "stocks": [_sr(s) for s in result.stocks],
        "upgrades": [_sr(s) for s in result.upgrades],
        "demotions": [_sr(s) for s in result.demotions],
        "report_file": result.report_file,
        "error": result.error,
    }


def decision_result_to_dict(result: DecisionResult) -> Dict[str, Any]:
    """将 DecisionResult 转为字典"""
    return {
        "success": result.success,
        "output": asdict(result.output) if result.output else None,
        "plans": [asdict(p) for p in result.plans],
        "main_tui": [asdict(p) for p in result.main_tui],
        "backup": [asdict(p) for p in result.backup],
        "no_action_reason": result.no_action_reason,
        "report_file": result.report_file,
        "error": result.error,
    }


# ═══════════════════════════════════════════════════
# JSON Schema（供 Structured Output 用）
# base_agent.call_llm(..., response_format={"type": "json_schema", "json_schema": {...}})
# ═══════════════════════════════════════════════════

SCREEN_SCHEMA = {
    "name": "ScreenResult",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "股票代码，如 600519"},
                        "name": {"type": "string", "description": "股票名称"},
                        "reason": {"type": "string", "description": "入选理由"},
                        "driver_level": {"type": "string", "enum": ["S级", "A级", "B级", "C级"]},
                        "pool": {"type": "string", "description": "所属板块/主题"}
                    },
                    "required": ["code", "name", "reason", "driver_level"]
                }
            }
        },
        "required": ["success", "candidates"]
    }
}

REVIEW_SCHEMA = {
    "name": "ReviewResult",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "name": {"type": "string"},
                        "composite_score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "confidence": {"type": "string", "enum": ["高", "中", "低"]},
                        "driver_level": {"type": "string"},
                        "dimensions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "dimension": {"type": "string"},
                                    "score": {"type": "integer"},
                                    "note": {"type": "string"}
                                }
                            }
                        },
                        "core_logic": {"type": "string"},
                        "flow_direction": {"type": "string", "enum": ["升级", "保留", "降级"]},
                        "target_pool": {"type": "string"}
                    },
                    "required": ["code", "name", "composite_score", "flow_direction"]
                }
            },
            "summary": {"type": "string", "description": "整体审查摘要"}
        },
        "required": ["success", "reviews"]
    }
}

DECISION_SCHEMA = {
    "name": "DecisionResult",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "symbol": {"type": "string", "description": "标的股票代码"},
            "decision": {"type": "string", "enum": ["买入", "观望", "放弃"], "description": "最终决策"},
            "action": {"type": "string", "description": "操作描述"},
            "position": {"type": "number", "description": "建议仓位 0.0-1.0"},
            "entry_price": {"type": "number", "description": "建议买入价"},
            "stop_loss": {"type": "number", "description": "止损触发价"},
            "target_1": {"type": "number", "description": "第一目标价"},
            "target_2": {"type": "number", "description": "第二目标价"},
            "trigger": {"type": "string", "description": "触发条件"},
            "invalidation": {"type": "string", "description": "失效条件"},
            "notes": {"type": "string", "description": "备注说明"}
        },
        "required": ["success", "symbol", "decision"]
    }
}
