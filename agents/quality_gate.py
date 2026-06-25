"""
QualityGate — 硬性质检门（方案B核心）
候选池升S级操作池前最后一道质检，纯规则、零LLM依赖。

质检链（按顺序执行，任一不通过即拒绝入池）：
  1. 市场状态 → 动态评分阈值
  2. 历史表现回溯（方案A）→ 推过亏钱的自动降分
  3. 过热二次检测 → 追高/暴涨拦截
  4. 连续推荐限制 → 同一只股N天内不重复推荐
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class QualityGate:
    """S级操作池硬性质检门 — 候选池升S池前的最后一道质检"""

    # 市场状态 → 动态准入阈值
    MARKET_SCORE_THRESHOLDS = {
        "偏空": 85,        # 偏空市场：≥85分才准入
        "震荡偏弱": 80,    # 震荡偏弱：≥80分
        "震荡": 78,        # 震荡市场：≥78分
        "震荡偏强": 75,    # 震荡偏强：≥75分（原标准）
        "偏多": 75,        # 偏多市场：≥75分（原标准）
    }

    # 历史亏损扣分（每亏3%扣1.5分，上限10分）
    HISTORY_PENALTY_PER_3PCT = 1.5
    HISTORY_PENALTY_MAX = 10

    # 连续推荐冷却期（天）
    RE_RECOMMEND_COOLDOWN_DAYS = 7

    # 历史记录文件路径（相对于root）
    HISTORY_DIR_NAME = "历史记录"

    def __init__(self, root: Path, logger=None):
        self.root = Path(root)
        self.history_dir = self.root / "data" / self.HISTORY_DIR_NAME
        self.logger = logger or self._null_logger

    def _null_logger(self, *args, **kwargs):
        pass

    def check(self, name: str, code: str, score: int,
              market_state: dict, decision_result: str = "",
              current_price: float = 0) -> dict:
        """一站式质检 — 返回 {"passed": bool, "reason": str, "adjusted_score": int}"""
        failures = []
        adjusted_score = score

        # ── 1. 市场状态 → 动态评分阈值 ─────────────────────────
        state = market_state.get("state", "震荡")
        min_score = self.MARKET_SCORE_THRESHOLDS.get(state, 75)
        if score < min_score:
            reason = f"市场状态[{state}]评分{score}分<动态阈值{min_score}分"
            failures.append(reason)
            print(f"[QualityGate] 🚫 {name}({code}) {reason}")

        # ── 2. 历史表现回溯（方案A） ────────────────────────────
        history = self._get_recommendation_history(code)
        if history:
            avg_return = history.get("avg_return", 0)
            if avg_return < -3:
                # 每亏3%扣1.5分，最多扣10分
                penalty = min(
                    int(abs(avg_return) / 3 * self.HISTORY_PENALTY_PER_3PCT),
                    self.HISTORY_PENALTY_MAX
                )
                adjusted_score = score - penalty
                if adjusted_score < min_score:
                    reason = f"历史亏损{avg_return:.1f}%扣{penalty}分({score}→{adjusted_score})<阈值{min_score}"
                    failures.append(reason)
                    print(f"[QualityGate] 🚫 {name}({code}) {reason}")
                else:
                    print(f"[QualityGate] ⚠️ {name}({code}) 历史亏损{avg_return:.1f}%扣{penalty}分({score}→{adjusted_score})")

            # 冷却期检查：上次推荐距今多久
            last_rec = history.get("last_recommend_date", "")
            if last_rec:
                try:
                    last_dt = datetime.strptime(last_rec, "%Y-%m-%d")
                    days_since = (datetime.now() - last_dt).days
                    if days_since < self.RE_RECOMMEND_COOLDOWN_DAYS:
                        reason = f"上次推荐{last_rec}距今仅{days_since}天<冷却{self.RE_RECOMMEND_COOLDOWN_DAYS}天"
                        failures.append(reason)
                        print(f"[QualityGate] 🚫 {name}({code}) {reason}")
                except ValueError:
                    pass

        # ── 3. 过热二次检测（调用 OverheatDetector） ────────────
        overheat = self._detect_overheat(code)
        if overheat:
            adjusted_score = max(adjusted_score - overheat.get("penalty", 0), 40)
            if adjusted_score < min_score:
                reason = f"过热拦截: {overheat.get('reason', '')} (扣{overheat.get('penalty', 0)}分)"
                failures.append(reason)
                print(f"[QualityGate] 🚫 {name}({code}) {reason}")

        # ── 4. 结论 ────────────────────────────────────────────
        if failures:
            return {
                "passed": False,
                "reason": "；".join(failures),
                "adjusted_score": adjusted_score,
            }
        return {
            "passed": True,
            "reason": f"质检通过（市场: {state} 评分: {score}→{adjusted_score} 动态阈值: {min_score}）",
            "adjusted_score": adjusted_score,
        }

    def _get_recommendation_history(self, code: str) -> Optional[dict]:
        """查询该股在天枢系统中的历史推荐记录（从回看报告/决策日志）"""
        # 源1：已存 full_verify.json（如果有）
        verify_file = self.root / "data" / "full_verify.json"
        if verify_file.exists():
            try:
                data = json.loads(verify_file.read_text(encoding="utf-8"))
                records = [s for s in data.get("stocks", []) if s.get("code") == code]
                if records:
                    returns = [r["r3"] for r in records if r.get("r3") is not None]
                    dates = [r["entry_date"] for r in records if r.get("entry_date")]
                    return {
                        "avg_return": sum(returns) / len(returns) if returns else 0,
                        "recommend_count": len(records),
                        "last_recommend_date": max(dates) if dates else "",
                        "worst_return": min(returns) if returns else 0,
                        "best_return": max(returns) if returns else 0,
                        "win_count": sum(1 for r in records if r.get("is_profit_3d")),
                        "loss_count": sum(1 for r in records if not r.get("is_profit_3d") and r.get("r3") is not None),
                    }
            except (json.JSONDecodeError, IOError):
                pass

        # 源2：从历史决策报告解析（兜底）— 只统计实际以【主推】入池的日期
        if self.history_dir.exists():
            records = []
            for fp in sorted(self.history_dir.glob("*_决策报告.md")):
                content = fp.read_text(encoding="utf-8", errors="replace")
                # 只匹配该股作为【主推】出现的情况，排除分析文本/备选中的提及
                push_pattern = rf'【主推】\s*[\u4e00-\u9fa5]{{2,6}}\s*[（(]{re.escape(code)}[）)]'
                if re.search(push_pattern, content):
                    m = re.search(r'(\d{4}-\d{2}-\d{2})', fp.name)
                    if m:
                        records.append({"date": m.group(1)})
            if records:
                return {
                    "recommend_count": len(records),
                    "avg_return": -5,  # 无精确行情数据，保守估算
                    "last_recommend_date": records[-1]["date"] if records else "",
                }
        return None

    def _detect_overheat(self, code: str) -> Optional[dict]:
        """过热二次检测 — 调用 OverheatDetector（如果当前有行情数据）"""
        try:
            from review_scorer import OverheatDetector
            # 从 shared_memory.json 获取行情数据
            sm_file = self.root / "data" / "shared_memory.json"
            if sm_file.exists():
                data = json.loads(sm_file.read_text(encoding="utf-8"))
                for s in data if isinstance(data, list) else []:
                    if str(s.get("代码", "")) == code:
                        return OverheatDetector.detect(
                            change_pct=float(s.get("涨跌幅", 0)),
                            pe_ttm=float(s.get("PE_TTM", 0)),
                            turnover=float(s.get("换手率", 0)),
                            volume_ratio=float(s.get("量比", 0)),
                            month_chg=float(s.get("月涨跌幅", 0)),
                            quarter_chg=float(s.get("季涨跌幅", 0)),
                            composite_score=int(s.get("评分", 0)),
                        )
        except ImportError:
            pass
        except Exception as e:
            print(f"[QualityGate] ⚠️ 过热检测异常({code}): {e}")
        return None
