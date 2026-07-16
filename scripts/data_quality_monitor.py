#!/usr/bin/env python3
"""数据质量监控脚本（每日运行，输出异常告警）

RF-004: bare except 治理
RF-009: 封装为可调用函数+定时任务
"""
import json, sys, traceback
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POOL = ROOT / "五池管理"
TODAY = datetime.now()


def check_pool(name: str) -> int:
    """检查单池数据质量"""
    try:
        d = json.load(open(POOL / f"{name}.json"))
        stocks = d.get('stocks', [])
        if not stocks and name not in ('持仓池', 'S级操作池'):
            issues.append(f"⚠️ {name}: 空池异常")
        codes = [s.get('代码') for s in stocks]
        if len(codes) != len(set(codes)):
            issues.append(f"❌ {name}: 存在重复代码")
        ut = d.get('统计', {}).get('更新日期', '')
        if ut:
            try:
                dt = datetime.strptime(ut[:10], '%Y-%m-%d')
                if (TODAY - dt).days > 3:
                    issues.append(f"⏰ {name}: 统计未更新{(TODAY - dt).days}天")
            except (ValueError, TypeError):
                pass  # 日期格式异常，静默跳过
        return len(stocks)
    except FileNotFoundError:
        issues.append(f"❌ {name}: 文件不存在")
        return 0
    except json.JSONDecodeError as e:
        issues.append(f"❌ {name}: JSON解析失败 {e}")
        return 0
    except Exception as e:
        issues.append(f"❌ {name}: 加载失败 {e}")
        return 0


def run_monitor() -> list:
    """执行数据质量监控，返回异常列表"""
    global issues
    issues = []
    pools = ['快筛候选池', '重点观察池', '边缘池', '持仓池', 'S级操作池', '重点观察池_历史池']
    for p in pools:
        check_pool(p)

    # 决策日志
    dl_path = DATA / "decision_log.json"
    if dl_path.exists():
        try:
            with open(dl_path) as f:
                dl = json.load(f)
            if isinstance(dl, list):
                total = len(dl)
                zero = sum(1 for x in dl if x.get('actual_pnl') in (0, '0', None))
                if total > 0 and zero / total > 0.8:
                    issues.append(f"⚠️ 决策日志: {zero}/{total} 无盈亏数据({zero / total * 100:.0f}%)")
        except (json.JSONDecodeError, IOError) as e:
            issues.append(f"❌ 决策日志读取失败: {e}")

    # 盟主持仓
    hp_path = DATA / "盟主持仓.json"
    if hp_path.exists():
        try:
            with open(hp_path) as f:
                hp = json.load(f)
            holdings = hp.get('持仓', [])
            if len(holdings) == 0:
                issues.append("ℹ️ 盟主持仓为空")
        except (json.JSONDecodeError, IOError) as e:
            issues.append(f"❌ 盟主持仓读取失败: {e}")

    # 熔断器
    cb_path = DATA / "circuit_breaker_state.json"
    if cb_path.exists():
        try:
            with open(cb_path) as f:
                cb = json.load(f)
            if cb.get('state') == 'open':
                issues.append(f"🔥 熔断器OPEN: {cb.get('consecutive_failures')}次连续失败")
        except (json.JSONDecodeError, IOError) as e:
            issues.append(f"❌ 熔断器读取失败: {e}")

    return issues


def main():
    issues = run_monitor()
    if issues:
        print("📊 数据质量监控报告", datetime.now().strftime("%Y-%m-%d %H:%M"))
        print("=" * 40)
        for i in issues:
            print(i)
    else:
        print("✅ 数据质量正常")


if __name__ == "__main__":
    main()