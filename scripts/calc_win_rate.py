#!/usr/bin/env python3
"""
P2：胜率计算脚本

读取 decision_log.json，统计：
- 总决策数
- 盈利数 / 亏损数 / 观望数
- 胜率（盈利 / (盈利+亏损)）

用法：
  python scripts/calc_win_rate.py
  python scripts/calc_win_rate.py --path /custom/path/decision_log.json
  python scripts/calc_win_rate.py --detail    # 输出详细结果列表
"""

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from agents.path_config import get_project_root
    PROJECT_ROOT = get_project_root()
except Exception:
    PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def load_decision_log(log_path: str = None) -> list:
    """加载 decision_log.json"""
    if log_path is None:
        log_path = PROJECT_ROOT / "data" / "decision_log.json"

    path = Path(log_path)
    if not path.exists():
        print(f"❌ 决策日志文件不存在: {path}")
        sys.exit(1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # 支持两种格式：顶层数组 或 {"决策记录": [...]}
        if isinstance(data, dict) and "决策记录" in data:
            data = data["决策记录"]
        if not isinstance(data, list):
            print(f"❌ 决策日志格式错误：期望 JSON 数组，得到 {type(data).__name__}")
            sys.exit(1)
        return data
    except json.JSONDecodeError as e:
        print(f"❌ 决策日志 JSON 解析失败: {e}")
        sys.exit(1)


def calc_win_rate(entries: list, show_detail: bool = False) -> dict:
    """
    计算胜率统计

    actual_pnl 字段判定规则：
      - > 0   → 盈利
      - <= 0  → 亏损
      - null/None/空 → 观望（尚无结果）

    支持中英文双字段名（中文兼容模式）
    """
    total = len(entries)
    profit = 0
    loss = 0
    watch = 0
    details = []

    for entry in entries:
        code = entry.get("code") or entry.get("股票代码", "?")
        name = entry.get("name") or entry.get("股票名称", "?")
        pnl = entry.get("actual_pnl") if entry.get("actual_pnl") is not None else entry.get("实际结果")
        recommendation = entry.get("recommendation") or entry.get("推荐操作", "?")
        date = entry.get("date") or entry.get("日期", "")

        if pnl is None or pnl == "":
            watch += 1
            verdict = "观望"
        elif float(pnl) > 0:
            profit += 1
            verdict = "盈利"
        else:
            loss += 1
            verdict = "亏损"

        if show_detail:
            details.append({
                "code": code,
                "name": name,
                "date": date,
                "recommendation": recommendation,
                "actual_pnl": pnl,
                "verdict": verdict,
            })

    # 有效决策 = 有明确盈亏结果的
    effective = profit + loss
    win_rate = round(profit / effective * 100, 2) if effective > 0 else 0.0

    return {
        "total": total,
        "profit": profit,
        "loss": loss,
        "watch": watch,
        "effective": effective,
        "win_rate": win_rate,
        "details": details,
    }


def print_report(stats: dict):
    """输出结构化报告"""
    print(f"\n{'='*50}")
    print(f"📊 决策胜率报告")
    print(f"{'='*50}")
    print(f"🕐 统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print(f"📈 总决策数:     {stats['total']}")
    print(f"✅ 盈利数:       {stats['profit']}")
    print(f"❌ 亏损数:       {stats['loss']}")
    print(f"⏳ 观望数:       {stats['watch']}")
    print(f"📋 有效决策数:   {stats['effective']}")
    print(f"🎯 胜率:         {stats['win_rate']}%")
    print()
    print(f"{'='*50}")

    # 结果分布
    if stats["total"] > 0:
        profit_pct = round(stats["profit"] / stats["total"] * 100, 1)
        loss_pct = round(stats["loss"] / stats["total"] * 100, 1)
        watch_pct = round(stats["watch"] / stats["total"] * 100, 1)
        print(f"📊 结果分布:")
        print(f"   盈利: {stats['profit']} ({profit_pct}%)")
        print(f"   亏损: {stats['loss']} ({loss_pct}%)")
        print(f"   观望: {stats['watch']} ({watch_pct}%)")
        print()

    # 详细列表
    if stats.get("details"):
        print(f"{'='*50}")
        print(f"📋 详细决策列表")
        print(f"{'='*50}")
        for d in stats["details"]:
            emoji = "✅" if d["verdict"] == "盈利" else "❌" if d["verdict"] == "亏损" else "⏳"
            pnl_str = f"{d['actual_pnl']:+.2f}" if d["actual_pnl"] is not None else "—"
            print(f"  {emoji} {d['name']}({d['code']}) | {d['date']} | {d['recommendation']} | PnL: {pnl_str}")
        print()

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="决策胜率计算器 — 读取 decision_log.json 输出结构化报告"
    )
    parser.add_argument(
        "--path", "-p",
        default=None,
        help="decision_log.json 路径（默认: data/decision_log.json）",
    )
    parser.add_argument(
        "--detail", "-d",
        action="store_true",
        help="输出详细决策列表",
    )
    parser.add_argument(
        "--multi", "-m",
        action="store_true",
        help="多窗口胜率对比（近7天/近30天/全量）",
    )
    args = parser.parse_args()

    entries = load_decision_log(args.path)
    print(f"📂 加载 {len(entries)} 条决策记录")

    if args.multi:
        from datetime import datetime, timedelta
        windows = [(7, "近7天"), (30, "近30天"), (0, "全量")]
        print(f"\n{'='*50}")
        print(f"📊 多窗口胜率对比")
        print(f"{'='*50}")
        print(f"🕐 统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        now = datetime.now()
        for days, label in windows:
            if days > 0:
                cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
                window_entries = [r for r in entries if r.get("date") and r.get("date") >= cutoff]
            else:
                window_entries = entries
            stats = calc_win_rate(window_entries, show_detail=False)
            marker = "▶" if days == 30 else " "
            print(f"{marker} {label}: 总{stats['effective']}笔 盈利{stats['profit']} 亏损{stats['loss']} 观望{stats['watch']} 胜率{stats['win_rate']}%")
        print()
        print(f"{'='*50}")
    else:
        stats = calc_win_rate(entries, show_detail=args.detail)
        print_report(stats)


if __name__ == "__main__":
    main()
