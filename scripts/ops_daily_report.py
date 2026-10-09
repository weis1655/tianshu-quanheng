#!/usr/bin/env python3
"""
天枢权衡 · 运营日报（WO-205）
每日自动生成：LLM调用费用、API成功率、修复次数、池状态变化

用法：
  python scripts/ops_daily_report.py              # 生成今日日报
  python scripts/ops_daily_report.py --date 2026-07-07  # 指定日期
  python scripts/ops_daily_report.py --output     # 输出到文件
"""
import sys
import json
import re
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths; ensure_agent_paths()
from logger import plog

REPORT_DIR = PROJECT_ROOT / "data" / "运营日报"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def collect_metrics(target_date: str) -> dict:
    """采集指定日期的运营指标"""
    metrics = {
        "date": target_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "llm_calls": 0,
        "api_success": 0,
        "api_failure": 0,
        "api_success_rate": 0,
        "p0_found": 0,
        "p0_fixed": 0,
        "dq_checks": 0,
        "dq_warnings": 0,
        "decision_loss_backtest": 0,
        "pool_changes": [],
        "decisions_made": 0,
        "auto_heal_prs": 0,
        "auto_heal_merged": 0,
        "errors": [],
    }

    # 1. 决策日志统计
    log_path = PROJECT_ROOT / "data" / "复盘记录" / "决策日志.json"
    if log_path.exists():
        try:
            log = json.loads(log_path.read_text(encoding="utf-8"))
            decisions = [r for r in log.get("决策记录", []) if r.get("日期", "").startswith(target_date)]
            metrics["decisions_made"] = len(decisions)
            # 近似LLM调用：每次决策1次LLM
            if decisions:
                metrics["llm_calls"] += len(decisions) * 2  # 审查+决策
        except Exception as e:
            plog("WARNING", f"[Ops] metrics 解析失败: {e}，跳过")

    # 2. 历史记录中的当日文件
    history_dir = PROJECT_ROOT / "data" / "历史记录"
    if history_dir.exists():
        day_files = list(history_dir.glob(f"{target_date}_*.md"))
        # LLM调用近似：每生成一个文件算一次LLM
        llm_files = [f for f in day_files if any(x in f.name for x in ["快筛", "审查", "决策", "质疑", "新闻", "宏观", "新闻分析"])]
        metrics["llm_calls"] += len(llm_files)
        # API成功率：检查是否有错误日志
        for f in day_files:
            try:
                text = f.read_text(encoding="utf-8")
                if "失败" in text or "error" in text.lower() or "❌" in text:
                    metrics["api_failure"] += 1
                else:
                    metrics["api_success"] += 1
            except Exception:
                pass

    # 3. 数据质量 P0（真 P0：读 dq_result 的 criticals，数据管道严重故障）
    dq_path = PROJECT_ROOT / "data" / "dq" / f"dq_result_{target_date}.json"
    if dq_path.exists():
        try:
            dq = json.loads(dq_path.read_text(encoding="utf-8"))
            summ = dq.get("summary", {}) if isinstance(dq, dict) else {}
            metrics["p0_found"] = int(summ.get("criticals", 0))
            metrics["dq_checks"] = int(summ.get("total_checks", 0))
            metrics["dq_warnings"] = int(summ.get("warnings", 0))
        except Exception:
            pass

    # 4. 决策质量反馈：回头看报告中的"实盘亏损"条数（降级，非 P0）
    review_dir = PROJECT_ROOT / "data" / "回顾报告"
    for report_path in sorted(review_dir.glob(f"{target_date}_回头看报告_v3*.md"), reverse=True):
        try:
            text = report_path.read_text(encoding="utf-8")
            metrics["decision_loss_backtest"] = len(re.findall(r"### 🔴 P0-实盘亏损", text))
            break
        except Exception:
            pass

    # 4. auto_heal 日志
    heal_log_path = PROJECT_ROOT / "data" / "历史记录" / f"{target_date}_auto_heal.json"
    if heal_log_path.exists():
        try:
            heal_data = json.loads(heal_log_path.read_text(encoding="utf-8"))
            for r in heal_data.get("results", []):
                if r.get("status") == "merged":
                    metrics["auto_heal_merged"] += 1
                elif r.get("status") in ("needs_review", "pushed_no_pr"):
                    metrics["auto_heal_prs"] += 1
        except Exception:
            pass

    # 5. 五池状态变化
    pool_dir = PROJECT_ROOT / "五池管理"
    if pool_dir.exists():
        for pool_file in sorted(pool_dir.glob("*.json")):
            try:
                pool_data = json.loads(pool_file.read_text(encoding="utf-8"))
                count = len(pool_data.get("stocks", []))
                metrics["pool_changes"].append({
                    "pool": pool_file.stem,
                    "count": count,
                })
            except Exception:
                pass

    # 计算成功率
    total_api = metrics["api_success"] + metrics["api_failure"]
    if total_api > 0:
        metrics["api_success_rate"] = round(metrics["api_success"] / total_api * 100, 1)

    return metrics


def collect_details(target_date: str) -> dict:
    """采集决策细节、推荐追踪、LLM退化、熔断器状态（补充日报空壳）"""
    details = {"decisions": [], "recommendations": [], "llm_degradation": None, "circuit_breaker": {}}
    try:
        for d in json.loads((PROJECT_ROOT / "data" / "decision_log.json").read_text(encoding="utf-8")):
            if d.get("date", "").startswith(target_date):
                details["decisions"].append({
                    "name": d.get("name", "?"), "code": d.get("code", "?"),
                    "tech_score": d.get("tech_score", ""), "confidence": d.get("confidence", ""),
                    "executed": d.get("$is_executed", False),
                    "hypothesis": (d.get("hypothesis") or "")[:60],
                })
    except Exception: pass
    try:
        rt = json.loads((PROJECT_ROOT / "data" / "recommendation_tracker.json").read_text(encoding="utf-8"))
        if isinstance(rt, list):
            for r in rt:
                if str(r.get("date", "")).startswith(target_date):
                    details["recommendations"].append({
                        "name": r.get("name", "?"), "code": r.get("code", "?"),
                        "type": r.get("type", "?"), "score": r.get("score", ""),
                    })
    except Exception: pass
    try:
        ld = json.loads((PROJECT_ROOT / "data" / "llm_degradation_counter.json").read_text(encoding="utf-8"))
        details["llm_degradation"] = {"count": ld.get("count", 0), "consecutive": ld.get("consecutive", 0), "latest": ld.get("latest", "")}
    except Exception: pass
    try:
        for k, v in json.loads((PROJECT_ROOT / "data" / "circuit_breaker_state.json").read_text(encoding="utf-8")).items():
            if isinstance(v, dict) and "state" in v:
                details["circuit_breaker"][k] = v["state"]
    except Exception: pass
    return details


def generate_report(metrics: dict, details: dict | None = None) -> str:
    """生成日报文本"""
    lines = []
    lines.append(f"# 🏛️ 天枢权衡 · 运营日报")
    lines.append(f"")
    lines.append(f"**日期：** {metrics['date']}")
    lines.append(f"**生成时间：** {metrics['generated_at']}")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## 📊 核心指标")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|:----:|")
    lines.append(f"| LLM 调用 | {metrics['llm_calls']} 次 |")
    lines.append(f"| API 成功率 | {metrics['api_success_rate']}% ({metrics['api_success']}/{metrics['api_success'] + metrics['api_failure']}) |")
    lines.append(f"| 决策数 | {metrics['decisions_made']} 次 |")
    lines.append(f"| 数据质量 P0 (严重) | {metrics['p0_found']} 个 |")
    lines.append(f"| 数据质量 WARN | {metrics.get('dq_warnings', 0)} 个 / {metrics['dq_checks']} 检查 |")
    lines.append(f"| 决策亏损回测 | {metrics.get('decision_loss_backtest', 0)} 条 |")
    lines.append(f"| Auto-Heal PR | {metrics['auto_heal_prs']} 个 |")
    lines.append(f"| Auto-Heal 合并 | {metrics['auto_heal_merged']} 个 |")
    lines.append(f"")

    # 五池状态
    if metrics["pool_changes"]:
        lines.append(f"## 🗂️ 五池状态")
        lines.append(f"")
        lines.append(f"| 池名称 | 数量 |")
        lines.append(f"|--------|:----:|")
        for p in metrics["pool_changes"]:
            lines.append(f"| {p['pool']} | {p['count']} 只 |")
        lines.append(f"")

    # 决策细节
    if details and details.get("decisions"):
        lines.append(f"## 🎯 今日决策明细")
        lines.append(f"")
        lines.append(f"| 名称 | 代码 | 技术分 | 置信度 | 已执行 | 假设 |")
        lines.append(f"|------|:----:|:------:|:------:|:------:|------|")
        for d in details["decisions"]:
            ex = "✅" if d["executed"] else "⏳"
            hyp = (d["hypothesis"] or "-")[:50]
            lines.append(f"| {d['name']} | {d['code']} | {d['tech_score']} | {d['confidence']} | {ex} | {hyp} |")
        lines.append(f"")

    # 推荐追踪
    if details and details.get("recommendations"):
        lines.append(f"## 📌 今日推荐追踪")
        lines.append(f"")
        for r in details["recommendations"]:
            lines.append(f"- **{r['type']}** {r['name']}({r['code']}) 评分={r['score']}")
        lines.append(f"")

    # LLM 退化 & 熔断器
    if details:
        ld = details.get("llm_degradation")
        cb = details.get("circuit_breaker")
        if ld or cb:
            lines.append(f"## 🤖 系统健康")
            lines.append(f"")
            if ld:
                flag = "⚠️" if ld["consecutive"] > 0 else "✅"
                lines.append(f"- {flag} LLM 退化: 累计 {ld['count']} 次, 连续 {ld['consecutive']} 次, 最近={ld['latest'] or '无'}")
            if cb:
                cb_str = ", ".join(f"{k}={v}" for k, v in cb.items())
                lines.append(f"- 熔断器: {cb_str}")
            else:
                lines.append(f"- 熔断器: 全部 closed（正常）")
            lines.append(f"")

    # 错误摘要
    if metrics["errors"]:
        lines.append(f"## ⚠️ 异常记录")
        lines.append(f"")
        for e in metrics["errors"][:10]:
            lines.append(f"- {e}")
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"*自动生成 · 天枢运营日报 v1*")
    lines.append(f"")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="天枢运营日报")
    parser.add_argument("--date", type=str, default=None, help="日报日期（默认今天）")
    parser.add_argument("--output", action="store_true", help="保存到文件")
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    metrics = collect_metrics(target_date)
    details = collect_details(target_date)
    report = generate_report(metrics, details)

    print(report)

    if args.output:
        output_path = REPORT_DIR / f"ops_report_{target_date}.md"
        output_path.write_text(report, encoding="utf-8")
        print(f"\n💾 已保存: {output_path}")


if __name__ == "__main__":
    main()