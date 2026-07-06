"""Review Scorer - 评分与过热检测模块（纯函数，零LLM依赖）

提供:
- OverheatDetector: 过热检测（6条规则，所有阈值集中管理为类常量）
"""

from typing import Optional


class OverheatDetector:
    """过热检测器 — 纯函数式静态方法，零LLM依赖

    所有阈值引用 thresholds.py 集中常量，避免双定义漂移。
    返回 dict 或 None，不入池不调LLM。
    """

    # ── 规则阈值从 thresholds.py 导入（SSOT）──
    from thresholds import (
        OVERHEAT_CRITICAL_DAY_CHG as CRITICAL_GAIN_PCT,
        OVERHEAT_CRITICAL_PE as CRITICAL_PE_LIMIT,
        OVERHEAT_CRITICAL_TURNOVER as CRITICAL_TURNOVER_PCT,
        OVERHEAT_CRITICAL_MONTH_CHG as MONTHLY_GAIN_PCT,
        OVERHEAT_CRITICAL_QUARTER_CHG as QUARTERLY_GAIN_PCT,
        OVERHEAT_W1_DAY_CHG as WARN1_GAIN_PCT,
        OVERHEAT_W1_SCORE as WARN1_SCORE,
        OVERHEAT_W2_DAY_CHG as WARN2_GAIN_PCT,
        OVERHEAT_W3_DAY_CHG as WARN3_GAIN_PCT,
        OVERHEAT_W3_VOL_RATIO as WARN3_VOLUME_RATIO,
        OVERHEAT_W4_AMPLITUDE as WARN4_AMPLITUDE_PCT,
        OVERHEAT_W4_MONTH_CHG as WARN4_MONTH_CHG_PCT,
        OVERHEAT_W4_SCORE as WARN4_SCORE,
    )

    # ── 以下为OverheatDetector私有常量（thresholds.py无对应项）──
    WARN2_SCORE_FALLBACK = 70     # 涨幅>8%且评分>70的独立WARNING
    PENALTY_CRITICAL = 30         # CRITICAL 扣30分
    PENALTY_WARN1 = 10            # WARNING-1 扣10分
    PENALTY_WARN2 = 5             # WARNING-2 扣5分
    PENALTY_WARN3 = 5             # WARNING-3 扣5分
    PENALTY_WARN4 = 10            # WARNING-4 扣10分

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
        amplitude: float = 0,       # 振幅（日内高波动指标）
        market_state: str = "震荡",
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
            market_state: 市场状态（偏多/震荡偏强时豁免WARNING规则）

        Returns:
            {
                "overheat_level": "critical" | "warning",
                "penalty": int,        # 应扣分数
                "reason": str,         # 检测理由
            }
            或 None（未触发任何规则）
        """

        # 强市状态下（偏多/震荡偏强）豁免WARNING规则，仅保留CRITICAL
        strong_market = market_state in ("偏多", "震荡偏强")
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

        # ── RULE 4: WARNING-1 — 涨幅>8% + 评分>75（强市豁免）────
        if not strong_market and (
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

        # ── RULE 5: WARNING-2 — 涨幅>=10%（强市豁免）─────────────
        if not strong_market and change_pct >= OverheatDetector.WARN2_GAIN_PCT:
            return {
                "overheat_level": OverheatDetector.LEVEL_WARNING,
                "penalty": OverheatDetector.PENALTY_WARN2,
                "reason": (
                    f"涨幅{change_pct:.1f}%过高，"
                    f"已扣{OverheatDetector.PENALTY_WARN2}分"
                ),
            }

        # ── RULE 5.5: WARNING-fallback — 涨幅>8%且评分>=70（强市豁免）──
        # 解决：涨幅>10%但PE≤80、换手≤12%、评分≤75、量比≤3时的漏检盲区
        # 放宽条件：涨幅>8%且评分>=70即可触发（修复：>= 而非 > 防止70分漏检）
        if not strong_market and (
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
        if not strong_market and (
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

        # ── RULE 7: WARNING-4 — 振幅>7% + 月涨>15% + 评分>=70（高波动过热）──
        # 解决：月涨>15%不足25%CRITICAL阈值、但振幅>7%高波动的漏检
        # 典型：300604长川科技（20日涨+15.31% + 振幅7.74 + 评分70）
        if not strong_market and (
            amplitude > OverheatDetector.WARN4_AMPLITUDE_PCT
            and month_chg > OverheatDetector.WARN4_MONTH_CHG_PCT
            and composite_score >= OverheatDetector.WARN4_SCORE
        ):
            return {
                "overheat_level": OverheatDetector.LEVEL_WARNING,
                "penalty": OverheatDetector.PENALTY_WARN4,
                "reason": (
                    f"振幅{amplitude:.1f}%高波动 + 月涨{month_chg:.1f}% + "
                    f"评分{composite_score}分 → 高波动过热，扣{OverheatDetector.PENALTY_WARN4}分"
                ),
            }

        return None
