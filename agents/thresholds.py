"""集中阈值管理 — 所有评分/过热/容量/时间阈值统一配置

使用方式:
    from agents.thresholds import *

本文件为单一真相来源（SSOT），所有 Agent 应从本文件导入阈值，
而非在代码中硬编码。迁移脚本见 CLAUDE.md 中的阈值参数保护区。
"""

# ═══════════════════════════════════════════════════════════════
# 评分等级阈值
# ═══════════════════════════════════════════════════════════════
SCORE_S_LEVEL = 90        # S级（极佳机会）
SCORE_A_LEVEL = 75        # A级（审查升级门槛 / 可关注）
SCORE_B_LEVEL = 65        # B级（黄色预警下限 / 谨慎观察）
SCORE_C_LEVEL = 55        # C级（观察区 / 建议暂缓）
SCORE_D_LEVEL = 0         # D级（淘汰）

# 评分等级标签映射
SCORE_LEVEL_LABELS = {
    (SCORE_S_LEVEL, 100): "S级",
    (SCORE_A_LEVEL, 89): "A级",
    (SCORE_B_LEVEL, 74): "B级(黄色预警)",
    (SCORE_C_LEVEL, 64): "C级(观察区)",
    (SCORE_D_LEVEL, 54): "D级(淘汰)",
}


# ═══════════════════════════════════════════════════════════════
# 黄色预警区间（LLM 备选观察用）
# ═══════════════════════════════════════════════════════════════
YELLOW_ALERT_MIN = 60     # 黄色预警下界
YELLOW_ALERT_MAX = 74     # 黄色预警上界


# ═══════════════════════════════════════════════════════════════
# 决策准入阈值
# ═══════════════════════════════════════════════════════════════
DECISION_MIN_SCORE = 75   # 决策执行最低分（≥75 → 可制定执行方案），动态调整见 DYNAMIC_THRESHOLDS
S_POOL_MIN_SCORE = 75     # S级操作池准入分（≥75 → 可入S池），弱市时由 QualityGate 动态提升
# 注：震荡偏强/偏多市场时与QualityGate阈值对齐(75)，弱市时QualityGate自动提升至80/85
KEY_WATCH_MIN_SCORE = 50  # 重点观察池准入分（gate_controller 准入规则）


# ═══════════════════════════════════════════════════════════════
# 动态评分阈值（A+B重构：市场状态→动态阈值）
# ═══════════════════════════════════════════════════════════════
# QualityGate 使用此映射决定弱市时是否提高准入分数
DYNAMIC_SCORE_THRESHOLDS = {
    "偏空": 85,        # 偏空市场≥85分才准入
    "震荡偏弱": 80,    # 震荡偏弱≥80分
    "震荡": 78,        # 震荡≥78分
    "震荡偏强": 75,    # 震荡偏强≥75分（原标准）
    "偏多": 75,        # 偏多≥75分（原标准）
}

# 历史亏损扣分（每亏3%扣1.5分，上限10分）
HISTORY_PENALTY_PER_3PCT = 1.5
HISTORY_PENALTY_MAX = 10

# 连续推荐冷却期（天）
RE_RECOMMEND_COOLDOWN_DAYS = 7


# ═══════════════════════════════════════════════════════════════
# 过热检测阈值
# ═══════════════════════════════════════════════════════════════

# CRITICAL — 强制降级
OVERHEAT_CRITICAL_DAY_CHG = 12     # 日涨幅 >12%
OVERHEAT_CRITICAL_PE = 80          # PE >80
OVERHEAT_CRITICAL_TURNOVER = 12    # 换手率 >12%
OVERHEAT_CRITICAL_MONTH_CHG = 25   # 月涨跌 >25%
OVERHEAT_CRITICAL_SCORE = 70       # 月涨触发时评分 >70（与 WARNING-1 的75不一致）
OVERHEAT_CRITICAL_QUARTER_CHG = 50 # 季涨跌 >50%

# WARNING-1 — 扣10分
OVERHEAT_W1_DAY_CHG = 8            # 日涨幅 >8%
OVERHEAT_W1_SCORE = 75             # 评分门槛 >75

# 盘中过热标记阈值（与W1语义不同：此阈值标记"本来不错但突然涨"的标的）
INTRADAY_OVERHEAT_MIN_SCORE = 70   # 评分≥70的标的才触发盘中过热标记

# WARNING-2 — 扣5分
OVERHEAT_W2_DAY_CHG = 10           # 日涨幅 >10%

# WARNING-3 — 扣5分（高位放量）
OVERHEAT_W3_DAY_CHG = 5            # 日涨幅 >5%
OVERHEAT_W3_VOL_RATIO = 3          # 量比 >3

# WARNING-4 — 扣10分（高波动+月涨>15%+评分>=70，新增RULE-7）
OVERHEAT_W4_AMPLITUDE = 7          # 振幅 >7% 高波动
OVERHEAT_W4_MONTH_CHG = 15         # 月涨幅 >15%
OVERHEAT_W4_SCORE = 70             # 评分 >=70


# ═══════════════════════════════════════════════════════════════
# 池容量
# ═══════════════════════════════════════════════════════════════
POOL_CAPACITY_FAST_SCREEN = 20     # 快筛候选池
POOL_CAPACITY_KEY_WATCH = 20       # 重点观察池
POOL_CAPACITY_S_POOL = 3           # S级操作池（≤3，P2升级：原2只）
POOL_CAPACITY_EDGE = 30            # 边缘池（P2升级：原20只）
# 持仓池无上限（None）

# 池容量字典（与 pool_manager.POOL_CAPACITY_LIMITS 保持一致）
POOL_CAPACITY_LIMITS = {
    "快筛候选池": POOL_CAPACITY_FAST_SCREEN,
    "重点观察池": POOL_CAPACITY_KEY_WATCH,
    "边缘池": POOL_CAPACITY_EDGE,
    "持仓池": None,
    "S级操作池": POOL_CAPACITY_S_POOL,
}


# ═══════════════════════════════════════════════════════════════
# 时间阈值
# ═══════════════════════════════════════════════════════════════
SCORE_DECAY_DAYS = 7               # 入池>7天开始衰减
SCORE_DECAY_PER_DAY = 0.5          # 每天衰减0.5分
SCORE_DECAY_MAX = 15               # 衰减上限15分
SCORE_DECAY_FLOOR = 40             # 衰减下限40分

CANDIDATE_EXPIRE_DAYS = 14         # 候选池14天淘汰
S_POOL_EXPIRE_DAYS = 1             # S级T+1过期


# ═══════════════════════════════════════════════════════════════
# 硬性降级阈值
# ═══════════════════════════════════════════════════════════════
HARD_DOWNGRADE_SCORE = 60          # <60分 → 强制降级边缘池
AUTO_DOWNGRADE_SCORE = 65          # <65分 → 存量扫描自动降级

# 一票否决降级上限（有风险信号时评分强制降至该值以下）
ONE_VETO_MAX_SCORE = 54            # ≤54，确保不会误升级


# ═══════════════════════════════════════════════════════════════
# 驱动等级加分
# ═══════════════════════════════════════════════════════════════
DRIVER_S_BONUS = 5                 # S级驱动 +5分
DRIVER_B_C_PENALTY = -5            # B/C级驱动 -5分

# 因子信号加分
FACTOR_SIGNAL_MIN = 3              # 信号≥3触发加分
FACTOR_SIGNAL_MULTIPLIER = 0.5     # 信号值×0.5
FACTOR_SIGNAL_MAX_BONUS = 3        # 上限+3分


# ═══════════════════════════════════════════════════════════════
# 其他
# ═══════════════════════════════════════════════════════════════
# 重点观察池信心度补填规则（pool_manager.enrich_confidence_for_existing_stocks）
CONFIDENCE_RULES = [
    (80, 100, "高"),
    (70, 79, "中高"),
    (60, 69, "中"),
    (0, 59, "低"),
]

# Skeptic 质疑连续阻塞降级阈值
SKEPTIC_BLOCK_LIMIT = 3            # 连续3次阻塞 → 自动降级边缘池

# 涨停/跌停判断（交易时段过滤）
TRADING_SESSION_START_HOUR = 9
TRADING_SESSION_MORNING_END = 11
TRADING_SESSION_AFTERNOON_START = 12
TRADING_SESSION_END = 15

# 48小时重复筛选防护
DEDUP_HOURS = 48

# 历史池最大保留量
HISTORY_POOL_MAX_STOCKS = 200

# 边缘池陈旧淘汰阈值（P2新增：入池>30天自动移除）
EDGE_POOL_STALE_DAYS = 30

# ═══════════════════════════════════════════════════════════════
# 仓位管理模板（F02 — 按市场状态定量限制）
# ═══════════════════════════════════════════════════════════════
POSITION_PCT_STRONG = 10     # 偏多/震荡偏强：单票≤10%
POSITION_PCT_NORMAL = 5      # 震荡：单票≤5%
POSITION_PCT_WEAK = 3        # 震荡偏弱/偏空：单票≤3%，且总仓位≤10%


# ═══════════════════════════════════════════════════════════════
# 统一评分等级函数（SSOT — 所有文件应从这里调用）
# ═══════════════════════════════════════════════════════════════
def score_to_level(score: float) -> str:
    """将综合分转换为评级等级，与 SCORE_LEVEL_LABELS 保持一致

    Args:
        score: 综合评分（0-100）

    Returns:
        等级标签：S级 / A级 / B级(黄色预警) / C级(观察区) / D级(淘汰)
    """
    if score >= SCORE_S_LEVEL:
        return "S级"
    if score >= SCORE_A_LEVEL:
        return "A级"
    if score >= SCORE_B_LEVEL:
        return "B级(黄色预警)"
    if score >= SCORE_C_LEVEL:
        return "C级(观察区)"
    return "D级(淘汰)"
