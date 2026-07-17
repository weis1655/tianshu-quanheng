"""TrackRecorder - 决策追踪记录独立模块"""
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from logger import plog
from path_config import ensure_agent_paths; ensure_agent_paths()


class TrackRecorder:
    """记录决策历史、闭环追踪、S级池历史评价"""

    def __init__(self, root: Path, history_dir: Path, pool_manager=None):
        self.root = root
        self.history_dir = history_dir
        self.pool_manager = pool_manager

    def inject_evo_history(self, scored_stocks: list) -> str:
        """注入候选股的历史决策摘要（记忆闭环）
        移植自 DecisionAgent._inject_evo_history 完整逻辑
        """
        try:
            sys.path.insert(0, str(self.root / "agents"))
            from path_config import get_review_evo
            ReviewEvo = get_review_evo()
        except Exception:
            return ""

        evo = ReviewEvo()
        lines = []
        for stock in scored_stocks:
            code = str(stock.get("code", stock.get("代码", ""))).strip()
            if not code:
                continue
            history = evo.get_stock_history(code, limit=2)
            if not history:
                continue
            name = stock.get("name", stock.get("名称", code))
            lines.append(f"【{name}({code}) 历史参考】")
            for h in history:
                date = h.get("date", "N/A")
                pnl = h.get("actual_pnl")
                reflection = h.get("反思", "")
                if pnl is not None:
                    lines.append(f"  - {date}: 盈亏{pnl:+.2f}%，{reflection}")
                else:
                    lines.append(f"  - {date}: {reflection or '无记录'}")
            lines.append("")

        if not lines:
            return ""
        return "\n## 历史决策参考（记忆闭环）\n" + "\n".join(lines) + "\n"

    def record_to_evo(self, scored_stocks: list, decision_result: str,
                      review_report: str = "", pools: dict = None,
                      hypothesis_extractor: Callable = None,
                      hypothesis_enhancer: Callable = None,
                      logger: object = None):
        """记录决策到复盘进化系统
        hypothesis_extractor: DecisionAgent._extract_hypothesis 的引用
        hypothesis_enhancer: DecisionAgent._enhance_hypothesis_from_decision 的引用
        """
        try:
            sys.path.insert(0, str(self.root / "agents"))
            from path_config import get_review_evo
            ReviewEvo = get_review_evo()
            evo = ReviewEvo(root=self.root)

            actionable = [s for s in scored_stocks if s.get("score", 0) >= 70]
            for s in actionable[:3]:  # 最多记录3只
                rec = "建议关注"
                if "买入" in decision_result or "建仓" in decision_result:
                    rec = "建议买入"

                # 提取假设（如果提供了extractor，使用DecisionAgent的方法）
                hypothesis = ""
                expected_logic = ""
                if hypothesis_extractor:
                    hypothesis, expected_logic = hypothesis_extractor(
                        s.get("code", ""), s.get("name", ""), review_report, pools=pools
                    )
                    # 增强假设
                    if hypothesis and "存在潜在驱动逻辑" in hypothesis:
                        if hypothesis_enhancer:
                            enhanced = hypothesis_enhancer(
                                s.get("code", ""), s.get("name", ""), decision_result
                            )
                            if enhanced:
                                hypothesis = enhanced

                evo.record_decision(
                    stock_code=s.get("code", ""),
                    stock_name=s.get("name", ""),
                    reason="评分通过决策门槛",
                    driver="AI审查",
                    tech_score=s.get("score", 0),
                    fundamental_score=s.get("score", 0),
                    recommendation=rec,
                    confidence=s.get("confidence", "中"),
                    hypothesis=hypothesis,
                    expected_logic=expected_logic,
                    is_executed='此方案由兜底引擎自动生成' not in decision_result,
                )
            plog("INFO", f"[TrackRecorder] ✅ 已记录 {len(actionable)} 只标的决策到复盘系统")
        except Exception as e:
            # 不用logger打印，因为logger可能不存在
            plog("INFO", f"[TrackRecorder] ⚠️ 记录决策异常: {e}")
    def evaluate_s_pool(self) -> Optional[str]:
        """评价S级操作池历史命中率，返回 Markdown 文本"""
        if not self.pool_manager:
            return None
        try:
            s_eval = self.pool_manager.evaluate_s_pool_history()
            if s_eval.get("evaluated", 0) == 0:
                return None
            return (
                f"\n\n## 📊 S级操作池历史评价\n"
                f"| 指标 | 数值 |\n"
                f"|------|------|\n"
                f"| 评价标的数 | {s_eval.get('evaluated', 0)} |\n"
                f"| 命中(≥3%) | {s_eval.get('hits', 0)} |\n"
                f"| 偏差(<-3%) | {s_eval.get('misses', 0)} |\n"
                f"| 命中率 | {s_eval.get('hit_rate', 'N/A')}% |\n"
                f"| 平均涨跌幅 | {s_eval.get('avg_change', 0):+.2f}% |\n"
            )
        except Exception as e:
            plog("INFO", f"[TrackRecorder] ⚠️ S级历史评价异常: {e}")
            return None

    def record_s_pool_eval(self, report: str, out_file: Path) -> str:
        """追加S级历史评价到决策报告"""
        eval_text = self.evaluate_s_pool()
        if eval_text:
            report += eval_text
            self._safe_write_text(out_file, report)
        return report

    def _safe_write_text(self, path: Path, content: str):
        path.write_text(content, encoding="utf-8")

    def record_closed_loop(self, plans: list):
        """记录闭环追踪"""
        try:
            sys.path.insert(0, str(self.root / "agents"))
            from closed_loop_tracker import ClosedLoopTracker
            tracker = ClosedLoopTracker()
            for plan in plans:
                tracker.record_decision(
                    code=plan.code,
                    name=plan.name,
                    priority=plan.priority,
                    plan={
                        "buy_method": plan.buy_method,
                        "position_pct": plan.position_pct,
                        "stop_loss": plan.stop_loss,
                        "target_1": plan.target_1_price,
                        "target_2": plan.target_2_price,
                    },
                )
        except Exception as e:
            plog("INFO", f"[TrackRecorder] ⚠️ 闭环追踪记录异常: {e}")