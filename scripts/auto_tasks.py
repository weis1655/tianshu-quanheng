#!/usr/bin/env python3
"""天枢自动化定时任务调度器

RF-005~010: 将手动脚本改造为定时任务
提供统一调度入口 + 手动触发兜底

用法：
  python scripts/auto_tasks.py                    # 执行所有任务
  python scripts/auto_tasks.py --task cost        # 只执行成本核算
  python scripts/auto_tasks.py --list             # 列出所有任务
"""
from __future__ import annotations

import sys, json, time, traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "data" / "auto_tasks"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_task(task_name: str, status: str, detail: str = "") -> None:
    """记录任务执行日志"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = LOG_DIR / f"{task_name}.log"
    with open(log_file, "a") as f:
        f.write(f"[{now}] {status} | {detail}\n")
    # 同时打印
    icon = "✅" if status == "OK" else "❌" if status == "FAIL" else "⏩"
    print(f"  {icon} [{task_name}] {detail}")


# ═══════════════════════════════════════════════════════════════
# 任务注册表
# ═══════════════════════════════════════════════════════════════

TASKS: Dict[str, Dict[str, Any]] = {}


def register(name: str, description: str, cron_schedule: str,
             func: Callable, enabled: bool = True):
    """注册定时任务"""
    TASKS[name] = {
        "name": name,
        "description": description,
        "cron": cron_schedule,
        "func": func,
        "enabled": enabled,
        "last_run": None,
        "last_status": None,
    }


def run_task(name: str) -> bool:
    """执行单个任务"""
    task = TASKS.get(name)
    if not task:
        print(f"❌ 未知任务: {name}")
        return False
    if not task["enabled"]:
        log_task(name, "SKIP", "任务已禁用")
        return False

    try:
        log_task(name, "RUN", "开始执行...")
        result = task["func"]()
        task["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task["last_status"] = "OK"
        detail = str(result)[:100] if result else "完成"
        log_task(name, "OK", detail)
        return True
    except Exception as e:
        task["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task["last_status"] = "FAIL"
        log_task(name, "FAIL", f"{e}")
        traceback.print_exc()
        return False


def run_all() -> Dict[str, bool]:
    """执行所有已启用任务"""
    results = {}
    for name, task in TASKS.items():
        if task["enabled"]:
            results[name] = run_task(name)
    return results


def list_tasks() -> List[Dict]:
    """列出所有任务"""
    return [{
        "name": t["name"],
        "description": t["description"],
        "cron": t["cron"],
        "enabled": t["enabled"],
        "last_run": t["last_run"],
        "last_status": t["last_status"],
    } for t in TASKS.values()]


# ═══════════════════════════════════════════════════════════════
# RF-005: 批量回测
# ═══════════════════════════════════════════════════════════════

def _task_backtest():
    """每周批量回测"""
    # 可复用backtest_workbench
    sys.path.insert(0, str(PROJECT_ROOT / "agents"))
    from backtest_workbench import StrategyParam, BatchWorkbench
    wb = BatchWorkbench()
    strategies = [
        StrategyParam(name="基准策略", min_score=75, stop_loss_pct=-5, take_profit_pct=15, hold_days=5),
        StrategyParam(name="保守策略", min_score=80, stop_loss_pct=-3, take_profit_pct=12, hold_days=3),
        StrategyParam(name="进取策略", min_score=70, stop_loss_pct=-7, take_profit_pct=20, hold_days=7),
    ]
    task = wb.run_batch(strategies, "定时回测")
    paths = wb.generate_reports(task.task_id)
    return f"回测完成: {task.task_id}, {len(paths)}份报告"

register("backtest", "每周策略回测", "0 9 * * 1", _task_backtest)


# ═══════════════════════════════════════════════════════════════
# RF-006: 成本核算
# ═══════════════════════════════════════════════════════════════

def _task_cost():
    """每日收盘成本核算"""
    try:
        from scripts.cost_accounting import main as cost_main
        cost_main()
        return "成本核算完成"
    except ImportError:
        # 直接调用
        exec(open(str(PROJECT_ROOT / "scripts" / "cost_accounting.py")).read())
        return "成本核算完成(直接执行)"

register("cost", "每日收盘成本核算", "30 15 * * 1-5", _task_cost)


# ═══════════════════════════════════════════════════════════════
# RF-008: 因子分析（每周）
# ═══════════════════════════════════════════════════════════════

def _task_factor():
    """每周因子分析"""
    try:
        from scripts.factor_analysis import main as fa_main
        fa_main()
        return "因子分析完成"
    except ImportError:
        exec(open(str(PROJECT_ROOT / "scripts" / "factor_analysis.py")).read())
        return "因子分析完成(直接执行)"

register("factor", "每周因子分析", "0 10 * * 1", _task_factor)


# ═══════════════════════════════════════════════════════════════
# RF-009: 数据质量监控（每日）
# ═══════════════════════════════════════════════════════════════

def _task_quality():
    """每日数据质量监控"""
    from scripts.data_quality_monitor import run_monitor
    issues = run_monitor()
    count = len(issues)
    if count > 0:
        for i in issues:
            print(f"  ⚠️ {i}")
    return f"数据质量检查: {count}个问题"

register("quality", "每日数据质量监控", "0 9 * * 1-5", _task_quality)


# ═══════════════════════════════════════════════════════════════
# RF-010: 退化监控（每日）
# ═══════════════════════════════════════════════════════════════

def _task_degradation():
    """每日策略退化监控"""
    try:
        from scripts.degradation_monitor import main as dg_main
        dg_main()
        return "退化监控完成"
    except ImportError:
        exec(open(str(PROJECT_ROOT / "scripts" / "degradation_monitor.py")).read())
        return "退化监控完成(直接执行)"

register("degradation", "每日策略退化监控", "30 9 * * 1-5", _task_degradation)


# ═══════════════════════════════════════════════════════════════
# CLI入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="天枢自动化任务调度器")
    parser.add_argument("--task", help="执行指定任务")
    parser.add_argument("--list", action="store_true", help="列出所有任务")
    parser.add_argument("--all", action="store_true", help="执行所有任务")
    args = parser.parse_args()

    if args.list:
        print(f"\n{'任务名':<15} {'描述':<20} {'Cron':<20} {'启用':<6} {'上次运行':<20} {'状态':<8}")
        print("-" * 90)
        for t in list_tasks():
            print(f"{t['name']:<15} {t['description']:<20} {t['cron']:<20} "
                  f"{'✅' if t['enabled'] else '❌':<6} "
                  f"{t['last_run'] or '-':<20} {t['last_status'] or '-':<8}")

    elif args.task:
        run_task(args.task)

    elif args.all:
        print(f"📋 执行所有定时任务 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        print("=" * 40)
        results = run_all()
        ok = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"\n{'='*40}")
        print(f"  {ok}/{total} 任务完成")

    else:
        # 默认执行所有任务
        print(f"📋 天枢自动化任务 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        print("=" * 50)
        results = run_all()
        ok = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"\n{'='*50}")
        print(f"  {ok}/{total} 任务完成")