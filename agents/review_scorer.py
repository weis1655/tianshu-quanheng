"""Review Scorer - 评分与过热检测模块（纯函数，零LLM依赖）

提供:
- OverheatDetector: 过热检测（6条规则，所有阈值集中管理为类常量）
"""

from typing import Optional


class OverheatDetector:
    """过热检测器 — 纯函数式静态方法，零LLM依赖

    所有硬编码阈值集中为类常量，方便统一调参。
    返回 dict 或 None，不入池不调LLM。
    """

    # ── CRITICAL 规则阈值 ────────────────────────────────────
    CRITICAL_GAIN_PCT = 12        # 日涨幅 > 12% 触发critical检查
    CRITICAL_PE_LIMIT = 80        # PE > 80 视为高估值
    CRITICAL_TURNOVER_PCT = 12    # 换手率 > 12% 视为高换手
    MONTHLY_GAIN_PCT = 25         # 月涨幅 > 25% 中期过热
    QUARTERLY_GAIN_PCT = 50       # 季涨幅 > 50% 长期暴涨

    # ── WARNING 规则阈值 ─────────────────────────────────────
    WARN1_GAIN_PCT = 8            # 涨幅 > 8% 触发warn-1
    WARN1_SCORE = 75              # 评分 > 75 才触发warn-1
    WARN2_GAIN_PCT = 10           # 涨幅 >= 10% 触发warn-2（边界修复：>= 而非 >）
    WARN2_SCORE_FALLBACK = 70     # 新增：涨幅>8%且评分>70的独立WARNING（P0-过热漏检修复）
    WARN3_GAIN_PCT = 5            # 涨幅 > 5% 触发warn-3
    WARN3_VOLUME_RATIO = 3        # 量比 > 3 视为高位放量

    # ── 惩罚分值 ─────────────────────────────────────────────
    PENALTY_CRITICAL = 30         # CRITICAL 扣30分
    PENALTY_WARN1 = 10            # WARNING-1 扣10分
    PENALTY_WARN2 = 5             # WARNING-2 扣5分
    PENALTY_WARN3 = 5             # WARNING-3 扣5分

    # ── 严重程度标识 ─────────────────────────────────────────
    LEVEL_CRITICAL = "critical"
    LEVEL_WARNING = "warning"

    @staticmethod
    def detect(
        change_pct: float,
        pe_ttm: float,
        turnover: float,
        volume_ratio: float,
        month_chg: float,
        quarter_chg: float,
        composite_score: int,
    ) -> Optional[dict]:
        """过热检测（纯函数，不入池不调LLM）

        Args:
            change_pct: 今日涨跌幅(%)
            pe_ttm: 市盈率TTM
            turnover: 换手率(%)
            volume_ratio: 量比
            month_chg: 月涨跌幅(%)
            quarter_chg: 季涨跌幅(%)
            composite_score: 综合评分(0-100)

        Returns:
            {
                "overheat_level": "critical" | "warning",
                "penalty": int,        # 应扣分数
                "reason": str,         # 检测理由
            }
            或 None（未触发任何规则）
        """
        # ── RULE 1: CRITICAL — 日涨幅>12% + (PE>80 或 换手>12%) ──
        if (
            change_pct > OverheatDetector.CRITICAL_GAIN_PCT
            and (
                pe_ttm > OverheatDetector.CRITICAL_PE_LIMIT
                or turnover > OverheatDetector.CRITICAL_TURNOVER_PCT
            )
        ):
            return {
                "overheat_level": OverheatDetector.LEVEL_CRITICAL,
                "penalty": OverheatDetector.PENALTY_CRITICAL,
                "reason": (
                    f"涨幅{change_pct:.1f}% + PE={pe_ttm:.0f} + "
                    f"换手{turnover:.1f}% → 强制降级"
                ),
            }

        # ── RULE 2: CRITICAL — 月涨跌>25% + 评分>=70（≥修复边界漏检）──
        if (
            month_chg > OverheatDetector.MONTHLY_GAIN_PCT
            and composite_score >= 70
        ):
            return {
                "overheat_level": OverheatDetector.LEVEL_CRITICAL,
                "penalty": OverheatDetector.PENALTY_CRITICAL,
                "reason": (
                    f"月涨跌{month_chg:.1f}%中期过热 + "
                    f"评分{composite_score}分 → 强制降级"
                ),
            }

        # ── RULE 3: CRITICAL — 季涨跌>50% ────────────────────────
        if quarter_chg > OverheatDetector.QUARTERLY_GAIN_PCT:
            return {
                "overheat_level": OverheatDetector.LEVEL_CRITICAL,
                "penalty": OverheatDetector.PENALTY_CRITICAL,
                "reason": (
                    f"季涨跌{quarter_chg:.1f}%长期暴涨 → 强制降级"
                ),
            }

        # ── RULE 4: WARNING-1 — 涨幅>8% + 评分>75 ────────────────
        if (
            change_pct > OverheatDetector.WARN1_GAIN_PCT
            and composite_score > OverheatDetector.WARN1_SCORE
        ):
            return {
                "overheat_level": OverheatDetector.LEVEL_WARNING,
                "penalty": OverheatDetector.PENALTY_WARN1,
                "reason": (
                    f"涨幅{change_pct:.1f}% + 评分{composite_score}分 "
                    f"→ 过热预警，扣{OverheatDetector.PENALTY_WARN1}分"
                ),
            }

        # ── RULE 5: WARNING-2 — 涨幅>=10%（边界修复：>= 而非 >）──────────────────
        if change_pct >= OverheatDetector.WARN2_GAIN_PCT:
            return {
                "overheat_level": OverheatDetector.LEVEL_WARNING,
                "penalty": OverheatDetector.PENALTY_WARN2,
                "reason": (
                    f"涨幅{change_pct:.1f}%过高，"
                    f"已扣{OverheatDetector.PENALTY_WARN2}分"
                ),
            }

        # ── RULE 5.5: WARNING-2  fallback — 涨幅>8%且评分>=70（P0-过热漏检修复）──
        # 解决：涨幅>10%但PE≤80、换手≤12%、评分≤75、量比≤3时的漏检盲区
        # 放宽条件：涨幅>8%且评分>=70即可触发（修复：>= 而非 > 防止70分漏检）
        if (
            change_pct > 8
            and composite_score >= OverheatDetector.WARN2_SCORE_FALLBACK
        ):
            return {
                "overheat_level": OverheatDetector.LEVEL_WARNING,
                "penalty": OverheatDetector.PENALTY_WARN2,
                "reason": (
                    f"涨幅{change_pct:.1f}% + 评分{composite_score}分（≥70）"
                    f"→ 过热预警（放宽阈值），扣{OverheatDetector.PENALTY_WARN2}分"
                ),
            }

        # ── RULE 6: WARNING-3 — 涨幅>5% + 量比>3（高位放量）────────
        if (
            change_pct > OverheatDetector.WARN3_GAIN_PCT
            and volume_ratio > OverheatDetector.WARN3_VOLUME_RATIO
        ):
            return {
                "overheat_level": OverheatDetector.LEVEL_WARNING,
                "penalty": OverheatDetector.PENALTY_WARN3,
                "reason": (
                    f"涨幅{change_pct:.1f}% + 量比{volume_ratio:.1f} "
                    f"→ 高位放量，扣{OverheatDetector.PENALTY_WARN3}分"
                ),
            }

        return None
