#!/usr/bin/env python3
"""
天枢权衡 — 数据质量日报

汇总 dq_scanner 的检测结果，生成数据质量日报并推送。
每日定时执行。

用法:
    python scripts/dq_daily_report.py                         # 扫描+生成日报
    python scripts/dq_daily_report.py --from-file dq_result.json  # 从已有结果生成
    python scripts/dq_daily_report.py --push                   # 生成+推送
"""

import os, sys, json, datetime, subprocess, argparse, glob
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DQ_DIR = PROJECT_ROOT / "data" / "dq"
DQ_DIR.mkdir(parents=True, exist_ok=True)
REPORT_FILE = DQ_DIR / f"dq_report_{datetime.date.today().isoformat()}.md"
RESULT_FILE = DQ_DIR / f"dq_result_{datetime.date.today().isoformat()}.json"

def run_scanner() -> dict:
    """运行数据质量扫描"""
    scanner = PROJECT_ROOT / "scripts" / "dq_scanner.py"
    if not scanner.exists():
        return {"error": "dq_scanner.py not found", "status": "error"}
    result = subprocess.run(
        [sys.executable, str(scanner)],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0 and result.returncode not in (0, 1, 2):
        return {"error": result.stderr, "status": "error"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "Cannot parse scanner output", "raw": result.stdout, "status": "error"}

def load_result(path: str) -> dict:
    """从文件加载扫描结果"""
    with open(path) as f:
        return json.load(f)

def generate_report(result: dict) -> str:
    """生成数据质量日报 Markdown"""
    today = datetime.date.today().isoformat()
    status = result.get("status", "unknown")
    status_emoji = {"healthy": "✅", "warning": "⚠️", "critical": "🔴", "error": "❌"}.get(status, "❓")

    lines = []
    lines.append(f"# 📊 天枢数据质量日报 | {today}")
    lines.append("")
    lines.append(f"**整体状态:** {status_emoji} {status.upper()}")
    lines.append(f"**扫描时间:** {result.get('timestamp', 'N/A')}")
    lines.append("")

    # 统计
    summary = result.get("summary", {})
    lines.append("## 统计概览")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|:----:|")
    lines.append(f"| 总检查项 | {summary.get('total_checks', 'N/A')} |")
    lines.append(f"| 通过 | {summary.get('passed', 'N/A')} |")
    lines.append(f"| 警告 | {summary.get('warnings', 'N/A')} |")
    lines.append(f"| 严重 | {summary.get('criticals', 'N/A')} |")
    lines.append(f"| 自动修复 | {summary.get('fixed', 'N/A')} |")
    lines.append("")

    # 按维度
    dims = result.get("dimensions", {})
    if dims:
        lines.append("## 五维质量指标")
        lines.append("")
        lines.append("| 维度 | 状态 | 检查项 | 异常数 |")
        lines.append("|:----|:----:|:------:|:------:|")
        for dim_name, dim_data in dims.items():
            d_status = dim_data.get("status", "?")
            d_emoji = {"ok": "✅", "warning": "⚠️", "critical": "🔴"}.get(d_status, "❓")
            d_checks = dim_data.get("total", 0)
            d_errors = dim_data.get("errors", 0)
            lines.append(f"| {dim_name} | {d_emoji} | {d_checks} | {d_errors} |")
        lines.append("")

    # 告警清单
    alerts = result.get("alerts", [])
    if alerts:
        lines.append("## ⚠️ 告警清单")
        lines.append("")
        for alert in alerts:
            level = alert.get("level", "?")
            le = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(level, "❓")
            check = alert.get("check", "?")
            detail = alert.get("detail", "?")
            lines.append(f"- {le} **[{level.upper()}]** {check}: {detail}")
        lines.append("")

    # 自愈记录
    fixes = result.get("fixes", [])
    if fixes:
        lines.append("## 🔧 自动修复记录")
        lines.append("")
        lines.append("| 时间 | 类型 | 对象 | 修复前 | 修复后 |")
        lines.append("|:----|:----|:-----|:-------|:-------|")
        for fix in fixes:
            lines.append(f"| {fix.get('time','?')} | {fix.get('type','?')} | {fix.get('target','?')} | {fix.get('before','?')} | {fix.get('after','?')} |")
        lines.append("")

    # 趋势（最近7天）
    lines.append("## 📈 质量趋势")
    lines.append("")
    lines.append("最近7天数据质量趋势（详见 dq_history.jsonl）：")
    lines.append("")
    history_file = DQ_DIR / "dq_history.jsonl"
    if history_file.exists():
        try:
            history = []
            with open(history_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        history.append(json.loads(line))
            history = history[-7:]
            lines.append("| 日期 | 状态 | 警告 | 严重 | 修复 |")
            lines.append("|:----|:----:|:----:|:----:|:----:|")
            for h in history:
                h_date = h.get("date", "?")
                h_status = h.get("status", "?")
                h_warn = h.get("warnings", 0)
                h_crit = h.get("criticals", 0)
                h_fix = h.get("fixed", 0)
                lines.append(f"| {h_date} | {h_status} | {h_warn} | {h_crit} | {h_fix} |")
        except Exception:
            lines.append("（历史数据解析失败）")
    else:
        lines.append("（首次运行，无历史数据）")
    lines.append("")

    lines.append("---")
    lines.append(f"*报告生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")

    return "\n".join(lines)

def save_history(result: dict):
    """追加历史记录"""
    history_file = DQ_DIR / "dq_history.jsonl"
    summary = result.get("summary", {})
    record = {
        "date": datetime.date.today().isoformat(),
        "timestamp": result.get("timestamp", ""),
        "status": result.get("status", "unknown"),
        "total_checks": summary.get("total_checks", 0),
        "passed": summary.get("passed", 0),
        "warnings": summary.get("warnings", 0),
        "criticals": summary.get("criticals", 0),
        "fixed": summary.get("fixed", 0)
    }
    with open(history_file, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def main():
    parser = argparse.ArgumentParser(description="天枢数据质量日报")
    parser.add_argument("--from-file", help="从已有JSON结果文件生成")
    parser.add_argument("--push", action="store_true", help="生成后推送")
    args = parser.parse_args()

    if args.from_file:
        result = load_result(args.from_file)
    else:
        result = run_scanner()
        # 保存扫描结果
        with open(RESULT_FILE, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    if "error" in result:
        print(f"❌ 扫描失败: {result.get('error')}")
        sys.exit(1)

    # 生成日报
    report = generate_report(result)
    with open(REPORT_FILE, "w") as f:
        f.write(report)

    # 保存历史
    save_history(result)

    print(report)
    print(f"\n📄 日报已保存: {REPORT_FILE}")
    print(f"📊 扫描结果: {RESULT_FILE}")

    # 推送（可选）
    if args.push:
        try:
            # 尝试通过飞书/邮件推送
            from send_email import send_email
            send_email(
                subject=f"📊 天枢数据质量日报 | {datetime.date.today().isoformat()}",
                html_body="",
                text_body=report,
                recipient="sjj139@139.com",
                skip_lock=True
            )
            print("📧 日报已推送")
        except ImportError:
            print("⚠️ send_email 模块不可用，跳过推送")
        except Exception as e:
            print(f"⚠️ 推送失败: {e}")

if __name__ == "__main__":
    main()