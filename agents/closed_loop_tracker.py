#!/usr/bin/env python3
"""
闭环追踪器（P2-3修复）
追踪每只股票在 快筛→审查→决策 全流程中的状态流转
用于：
1. 识别快筛推荐但审查降级的"误推"
2. 识别审查升级但决策未采用的"漏用"
3. 统计各环节准确率
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from safe_file_utils import safe_read_json, safe_write_file
from logger import plog

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
TRACKING_DIR = PROJECT_ROOT / "data" / "闭环追踪"


class ClosedLoopTracker:
    """快筛-审查-决策闭环追踪器"""

    def __init__(self, tracking_dir: Path = None):
        self.tracking_dir = tracking_dir or TRACKING_DIR
        self.tracking_dir.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.tracking_file = self.tracking_dir / f"{self.today}_闭环追踪.json"
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.tracking_file.exists():
            data = safe_read_json(self.tracking_file, default=None, log_error=False)
            if data is not None:
                return data
            logger.warning(f"[ClosedLoopTracker] 追踪文件JSON解析失败，使用空数据: {self.tracking_file}")
        return {"date": self.today, "stocks": {}}

    def _save(self):
        success = safe_write_file(
            self.tracking_file,
            json.dumps(self.data, ensure_ascii=False, indent=2)
        )
        if not success:
            logger.error(f"[ClosedLoopTracker] 保存追踪数据失败: {self.tracking_file}")

    def record_screen(self, code: str, name: str, reason: str, driver_level: str):
        """记录快筛推荐"""
        if code not in self.data["stocks"]:
            self.data["stocks"][code] = {
                "code": code,
                "name": name,
                "screen": None,
                "review": None,
                "decision": None,
            }
        self.data["stocks"][code]["screen"] = {
            "name": name,
            "reason": reason,
            "driver_level": driver_level,
            "timestamp": datetime.now().isoformat(),
        }
        self._save()

    def record_review(self, code: str, name: str, score: int, level: str, 
                      flow_direction: str, target_pool: str,
                      action_advice: str = ""):
        """记录审查结果"""
        if code not in self.data["stocks"]:
            self.data["stocks"][code] = {
                "code": code,
                "name": name,
                "screen": None,
                "review": None,
                "decision": None,
            }
        self.data["stocks"][code]["review"] = {
            "score": score,
            "level": level,
            "flow_direction": flow_direction,
            "target_pool": target_pool,
            "action_advice": action_advice,
            "timestamp": datetime.now().isoformat(),
        }
        self._save()

    def record_decision(self, code: str, name: str, priority: str, 
                        plan: Optional[Dict[str, Any]] = None):
        """记录决策结果"""
        if code not in self.data["stocks"]:
            self.data["stocks"][code] = {
                "code": code,
                "name": name,
                "screen": None,
                "review": None,
                "decision": None,
            }
        self.data["stocks"][code]["decision"] = {
            "priority": priority,
            "plan": plan,
            "timestamp": datetime.now().isoformat(),
        }
        self._save()

    # ── P2-2：T+1 追踪机制 ──────────────────────────────────
    def record_t1_performance(self, code: str, t1_date: str, 
                               t1_open: float, t1_close: float,
                               decision_price: float, 
                               stop_loss: float, target_1: float):
        """
        记录T+1表现：验证审查升级/降级的后续表现
        """
        if code not in self.data["stocks"]:
            self.data["stocks"][code] = {"code": code, "name": "", 
                                          "screen": None, "review": None, "decision": None}
        
        # 计算T+1盈亏
        pnl_pct = round((t1_close - decision_price) / decision_price * 100, 2)
        hit_stop_loss = t1_close <= stop_loss if stop_loss else False
        hit_target_1 = t1_close >= target_1 if target_1 else False
        
        self.data["stocks"][code]["t1_performance"] = {
            "t1_date": t1_date,
            "t1_open": t1_open,
            "t1_close": t1_close,
            "decision_price": decision_price,
            "stop_loss": stop_loss,
            "target_1": target_1,
            "pnl_pct": pnl_pct,
            "hit_stop_loss": hit_stop_loss,
            "hit_target_1": hit_target_1,
            "verdict": self._classify_t1_verdict(pnl_pct, hit_stop_loss, hit_target_1),
            "timestamp": datetime.now().isoformat(),
        }
        self._save()

    def _classify_t1_verdict(self, pnl_pct: float, hit_stop_loss: bool, hit_target_1: bool) -> str:
        """分类T+1表现"""
        if hit_stop_loss:
            return "止损触发 ❌"
        elif hit_target_1:
            return "达成目标 ✅"
        elif pnl_pct > 0:
            return "盈利 ✔️"
        elif pnl_pct < 0:
            return "亏损 ⚠️"
        else:
            return "平盘 —"

    def get_t1_summary(self) -> Dict[str, Any]:
        """获取T+1表现统计"""
        t1_records = []
        for code, record in self.data["stocks"].items():
            t1 = record.get("t1_performance")
            if t1:
                t1_records.append({
                    "code": code,
                    "name": record.get("name", ""),
                    **t1,
                })
        
        if not t1_records:
            return {"total": 0, "message": "暂无T+1数据"}
        
        profits = [r for r in t1_records if r["pnl_pct"] > 0]
        losses = [r for r in t1_records if r["pnl_pct"] < 0]
        stop_hits = [r for r in t1_records if r["hit_stop_loss"]]
        target_hits = [r for r in t1_records if r["hit_target_1"]]
        
        avg_pnl = round(sum(r["pnl_pct"] for r in t1_records) / len(t1_records), 2)
        win_rate = round(len(profits) / len(t1_records) * 100, 1)
        
        return {
            "total": len(t1_records),
            "profits": len(profits),
            "losses": len(losses),
            "stop_loss_hits": len(stop_hits),
            "target_hits": len(target_hits),
            "avg_pnl_pct": avg_pnl,
            "win_rate": win_rate,
            "records": t1_records,
        }

    def generate_t1_report(self) -> str:
        """生成T+1表现验证报告"""
        summary = self.get_t1_summary()
        if summary.get("total", 0) == 0:
            return f"# 【T+1表现验证报告】{self.today}\n\n暂无T+1数据，等待次日收盘后更新。"
        
        lines = [
            f"# 【T+1表现验证报告】{self.today}",
            "",
            "## 📊 总体统计",
            f"- 追踪标的：{summary['total']} 只",
            f"- 盈利：{summary['profits']} 只 | 亏损：{summary['losses']} 只",
            f"- 止损触发：{summary['stop_loss_hits']} 次 | 达成目标：{summary['target_hits']} 次",
            f"- 平均盈亏：{summary['avg_pnl_pct']:+.2f}% | 胜率：{summary['win_rate']}%",
            "",
            "## 📋 逐只表现",
            "| 代码 | 名称 | 决策价 | T+1收盘 | 盈亏% | 止损 | 目标 | 判定 |",
            "|------|------|--------|---------|-------|------|------|------|",
        ]
        for r in summary["records"]:
            lines.append(
                f"| {r['code']} | {r['name']} | {r['decision_price']:.2f} | "
                f"{r['t1_close']:.2f} | {r['pnl_pct']:+.2f}% | "
                f"{'✅' if r['hit_stop_loss'] else '—'} | "
                f"{'✅' if r['hit_target_1'] else '—'} | "
                f"{r['verdict']} |"
            )
        
        # 审查升级准确率
        upgrade_correct = 0
        upgrade_total = 0
        for r in summary["records"]:
            record = self.data["stocks"][r["code"]]
            review = record.get("review", {})
            if review.get("flow_direction") == "升级":
                upgrade_total += 1
                if r["pnl_pct"] > 0:
                    upgrade_correct += 1
        
        if upgrade_total > 0:
            upgrade_acc = round(upgrade_correct / upgrade_total * 100, 1)
            lines.append("")
            lines.append("## 🎯 审查升级准确率")
            lines.append(f"- 升级后T+1盈利：{upgrade_correct}/{upgrade_total} = {upgrade_acc}%")
        
        lines.append("")
        lines.append(f"生成时间：{datetime.now().strftime('%H:%M:%S')}")
        return "\n".join(lines)
    # ────────────────────────────────────────────────────────

    def get_anomalies(self) -> List[Dict[str, Any]]:
        """
        获取异常流转记录：
        1. 快筛推荐但审查降级（误推）
        2. 审查升级但决策未采用（漏用）
        3. 审查回避但决策推荐（越权）
        """
        anomalies = []
        for code, record in self.data["stocks"].items():
            screen = record.get("screen")
            review = record.get("review")
            decision = record.get("decision")

            # 异常1：快筛推荐但审查降级
            if screen and review and review.get("flow_direction") == "降级":
                anomalies.append({
                    "code": code,
                    "name": record.get("name", ""),
                    "type": "误推",
                    "severity": "high",
                    "detail": f"快筛推荐({screen.get('driver_level')}) → 审查降级({review.get('score')}分)",
                })

            # 异常2：审查升级但决策未采用
            if review and review.get("flow_direction") == "升级" and not decision:
                anomalies.append({
                    "code": code,
                    "name": record.get("name", ""),
                    "type": "漏用",
                    "severity": "medium",
                    "detail": f"审查升级({review.get('score')}分) → 决策未采用",
                })

            # 异常3：审查回避但决策推荐
            if review and decision and review.get("action_advice") == "回避":
                priority = decision.get("priority", "")
                if priority in ["主推", "推荐"]:
                    anomalies.append({
                        "code": code,
                        "name": record.get("name", ""),
                        "type": "越权",
                        "severity": "critical",
                        "detail": f"审查回避 → 决策{priority}",
                    })

        return anomalies

    def get_summary(self) -> Dict[str, Any]:
        """获取闭环统计摘要"""
        total = len(self.data["stocks"])
        screened = sum(1 for s in self.data["stocks"].values() if s.get("screen"))
        reviewed = sum(1 for s in self.data["stocks"].values() if s.get("review"))
        decided = sum(1 for s in self.data["stocks"].values() if s.get("decision"))

        anomalies = self.get_anomalies()
        by_type = {}
        for a in anomalies:
            t = a["type"]
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "date": self.today,
            "total_candidates": total,
            "screened": screened,
            "reviewed": reviewed,
            "decided": decided,
            "anomalies": len(anomalies),
            "anomalies_by_type": by_type,
        }

    def generate_report(self) -> str:
        """生成闭环追踪报告"""
        summary = self.get_summary()
        anomalies = self.get_anomalies()

        lines = [
            f"# 【闭环追踪报告】{self.today}",
            "",
            "## 📊 流转统计",
            f"- 快筛推荐：{summary['screened']} 只",
            f"- 审查完成：{summary['reviewed']} 只",
            f"- 决策完成：{summary['decided']} 只",
            f"- 异常记录：{summary['anomalies']} 条",
            "",
        ]

        if anomalies:
            lines.append("## ⚠️ 异常流转详情")
            for a in anomalies:
                severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(a["severity"], "⚪")
                lines.append(f"- {severity_icon} **{a['name']}({a['code']})** [{a['type']}]：{a['detail']}")
        else:
            lines.append("## ✅ 无异常流转")

        lines.append("")
        lines.append("---")
        lines.append(f"生成时间：{datetime.now().strftime('%H:%M:%S')}")

        return "\n".join(lines)


if __name__ == "__main__":
    # 测试
    tracker = ClosedLoopTracker()
    tracker.record_screen("600519", "贵州茅台", "白酒龙头，估值修复", "A级")
    tracker.record_review("600519", "贵州茅台", 72, "B级", "升级", "重点观察池")
    tracker.record_decision("600519", "贵州茅台", "主推", {"buy_price": 1700})
    
    plog("INFO", tracker.generate_report())