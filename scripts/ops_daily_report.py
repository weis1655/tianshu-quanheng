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
        except Exception:
            pass

    # 2. 历史记录中的当日文件
    history_dir = PROJECT_ROOT / "data" / "历史记录"
    if history_dir.exists():
        day_files = list(history_dir.glob(f"{target_date}_*.md"))
        # LLM调用近似：每生成一个文件算一次LLM
        llm_files = [f for f in day_files if any(x in f.name for x in ["快筛", "审查", "决策", "质疑", "新闻"])]
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

    # 3. 回头看报告中的P0
    review_dir = PROJECT_ROOT / "data" / "回顾报告"
    for report_path in sorted(review_dir.glob(f"{target_date}_回头看报告_v3*.md"), reverse=True):
        try:
            text = report_path.read_text(encoding="utf-8")
            metrics["p0_found"] = len(re.findall(r"### 🔴 P0-", text))
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


def generate_report(metrics: dict) -> str:
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
    lines.append(f"| P0 发现 | {metrics['p0_found']} 个 |")
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
    report = generate_report(metrics)

    print(report)

    if args.output:
        output_path = REPORT_DIR / f"ops_report_{target_date}.md"
        output_path.write_text(report, encoding="utf-8")
        print(f"\n💾 已保存: {output_path}")


if __name__ == "__main__":
    main()