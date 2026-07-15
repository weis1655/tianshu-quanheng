#!/usr/bin/env python3
"""
天枢权衡 · 策略回测沙箱（WO-204）
离线回测 + 新旧策略对比验证

用法：
  python scripts/backtest_sandbox.py                          # 默认回测（最近60天）
  python scripts/backtest_sandbox.py --days 90                # 自定义窗口
  python scripts/backtest_sandbox.py --strategy '{"min_score": 70}'  # 自定义策略
  python scripts/backtest_sandbox.py --compare                # 新旧策略对比
  python scripts/backtest_sandbox.py --approve                # 通过验证后确认上线
"""
import sys
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

SANDBOX_DIR = PROJECT_ROOT / "data" / "回测沙箱"
SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

# ── 默认策略参数 ─────────────────────────────────────
DEFAULT_STRATEGY = {
    "name": "当前策略(v6.2)",
    "min_score": 75,
    "min_score_weak_market": 85,
    "max_position_pct": 10,
    "max_positions_per_sector": 2,
    "stop_loss_pct": -5,
    "take_profit_pct": 15,
    "hold_days": 3,
    "require_skeptic_pass": True,
    "require_upgrade": True,
    "slippage_pct": 0.02,         # 滑点成本（成交额%）+ 冲击成本
    "slippage_min_pts": 0.01,     # 最小滑点（价格点数）
}

NEW_STRATEGY = {
    "name": "新策略(待验证)",
    "min_score": 75,
    "min_score_weak_market": 80,
    "max_position_pct": 10,
    "max_positions_per_sector": 2,
    "stop_loss_pct": -5,
    "take_profit_pct": 15,
    "hold_days": 3,
    "require_skeptic_pass": True,
    "require_upgrade": True,
    "slippage_pct": 0.02,
    "slippage_min_pts": 0.01,
}


# ── 历史数据加载 ──────────────────────────────────────
def load_historical_decisions(days=60) -> list:
    """从决策日志和回头看报告中加载历史交易记录"""
    records = []
    
    # Source 1: 决策日志 JSON（含已验证记录）
    log_path = PROJECT_ROOT / "data" / "复盘记录" / "决策日志.json"
    if log_path.exists():
        try:
            log = json.loads(log_path.read_text(encoding="utf-8"))
            for r in log.get("决策记录", []):
                pnl_val = r.get("实际结果")
                try:
                    pnl_val = float(pnl_val) if pnl_val not in (None, "") else 0
                except (ValueError, TypeError):
                    pnl_val = 0
                records.append({
                        "code": r.get("股票代码", ""),
                        "name": r.get("股票名称", ""),
                        "date": r.get("日期", ""),
                        "score": r.get("评分", r.get("技术面评分", 0)),
                        "pnl": pnl_val,
                        "source": "decision_log",
                    })
            # 加入已验证盈亏记录
            for v in log.get("验证记录", []):
                records.append({
                    "code": v.get("code", ""),
                    "name": v.get("name", ""),
                    "date": v.get("decision_date", ""),
                    "pnl": v.get("actual_pnl_pct", 0),
                    "score": 80,  # 已验证标的默认高分
                    "source": "verified",
                })
        except Exception as e:
            print(f"  ⚠️ 决策日志加载失败: {e}")

    # Source 2: 回头看报告中的P0实盘亏损记录
    review_dir = PROJECT_ROOT / "data" / "回顾报告"
    for report_path in sorted(review_dir.glob("*_回头看报告_v3*.md"), reverse=True)[:3]:
        try:
            text = report_path.read_text(encoding="utf-8")
            # 解析P0-实盘亏损表格
            # 格式: | 日期 | 2026-06-04 | → | 代码 | 601138 | → | 名称 | XX | → | 说明 | 跌幅-X%
            sections = text.split("### 🔴 P0-实盘亏损")
            for section in sections[1:]:
                lines = section.strip().split("\n")
                entry = {}
                for line in lines:
                    if "|" in line and "---" not in line:
                        parts = [p.strip() for p in line.split("|") if p.strip()]
                        if len(parts) >= 2:
                            key, val = parts[0], parts[1]
                            if key == "日期":
                                entry["date"] = val
                            elif key == "代码":
                                entry["code"] = val
                            elif key == "名称":
                                entry["name"] = val
                            elif key == "说明":
                                m = re.search(r"跌幅([-\d.]+)%", val)
                                if m:
                                    entry["pnl"] = float(m.group(1))
                                    records.append({
                                        "code": entry.get("code", ""),
                                        "name": entry.get("name", ""),
                                        "date": entry.get("date", ""),
                                        "pnl": entry["pnl"],
                                        "score": 0,
                                        "source": "review_p0_loss",
                                    })
        except Exception:
            pass

    # Source 3: 回头看报告中的审查升级标的收益
    for report_path in sorted(review_dir.glob("*_回头看报告_v3*.md"), reverse=True)[:1]:
        try:
            text = report_path.read_text(encoding="utf-8")
            for m in re.finditer(
                r"\|\s*([\u4e00-\u9fa5]{2,6})\s+(\d{6})\s*\|\s*[✅❌]?\s*([+-]?\d+\.?\d*)%",
                text
            ):
                name, code, pnl_str = m.group(1), m.group(2), m.group(3)
                records.append({
                    "code": code,
                    "name": name,
                    "date": report_path.stem[:10],
                    "pnl": float(pnl_str),
                    "score": 80,  # 审查升级默认≥75分
                    "source": "review_upgrade",
                })
        except Exception:
            pass

    # Source 4: 标准化决策日志 data/decision_log.json（优先使用有实际盈亏的记录）
    std_log = PROJECT_ROOT / "data" / "decision_log.json"
    if std_log.exists():
        try:
            std = json.loads(std_log.read_text(encoding="utf-8"))
            if isinstance(std, list):
                for r in std:
                    ts = r.get("tech_score", 0) or 0
                    fs = r.get("fundamental_score", 0) or 0
                    score = max(ts, fs)
                    pnl = r.get("actual_pnl")
                    if pnl not in (None, 0, "", 0.0):
                        records.append({
                            "code": r.get("code", ""),
                            "name": r.get("name", ""),
                            "date": r.get("date", ""),
                            "pnl": float(pnl),
                            "score": score,
                            "source": "std_log",
                        })
        except Exception as e:
            print(f"  ⚠️ 标准化决策日志加载失败: {e}")

    # 去重
    seen = set()
    unique = []
    for r in records:
        key = f"{r['code']}_{r['date']}"
        if key not in seen:
            seen.add(key)
            unique.append(r)
    
    return unique


def backtest(strategy: dict, records: list) -> dict:
    """运行回测"""
    results = {
        "strategy": strategy["name"],
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl": 0.0,
        "max_drawdown": 0.0,
        "avg_pnl_per_trade": 0.0,
        "win_rate": 0.0,
        "by_score_range": defaultdict(list),
        "top_gainers": [],
        "top_losers": [],
    }

    min_score = strategy["min_score"]
    hold_days = strategy["hold_days"]
    stop_loss = strategy["stop_loss_pct"]
    slippage_pct = strategy.get("slippage_pct", 0.02)
    slippage_min_pts = strategy.get("slippage_min_pts", 0.01)

    for r in records:
        # 过滤条件
        score = r.get("score", 0)
        try:
            score = float(score)
        except (ValueError, TypeError):
            score = 0
        
        if score < min_score:
            continue  # 低分跳过

        pnl = r.get("pnl", 0)
        try:
            pnl = float(pnl)
        except (ValueError, TypeError):
            pnl = 0

        if pnl == 0:
            continue  # 未验证跳过

        # 涨跌停成交概率过滤：极端涨跌幅（>9%）的标的成交概率降低
        limit_prob = 1.0
        if abs(pnl) >= 9.0:
            limit_prob = 0.3  # 涨停/跌停附近仅30%概率成交
        elif abs(pnl) >= 7.0:
            limit_prob = 0.7  # 近涨停/跌停70%概率成交
        import random
        # ── B03: GateController阻塞模拟 ────────────────────
        # 实盘历史：约15%的标的被GateController拦截（阻塞降级）
        if random.random() < 0.15:
            continue

        # ── B02: 涨跌停成交概率过滤 ────────────────────────
        limit_prob = 1.0
        if abs(pnl) >= 9.0:
            limit_prob = 0.3
        elif abs(pnl) >= 7.0:
            limit_prob = 0.7
        if random.random() > limit_prob:
            continue

        # ── B05: 环境延迟折损 — 0.2%收益损耗 ───────────────
        delay_cost = abs(pnl) * 0.002

        results["total_trades"] += 1
        # 扣除滑点与冲击成本（双边：买入+卖出）
        slippage_cost = max(abs(pnl) * slippage_pct / 100, slippage_min_pts)
        adj_pnl = pnl - slippage_cost - delay_cost

        # ── B04: 风控链 — 止损/止盈截断 ────────────────────
        if adj_pnl < stop_loss:
            adj_pnl = stop_loss
        if adj_pnl > strategy.get("take_profit_pct", 20):
            adj_pnl = strategy.get("take_profit_pct", 20)

        results["total_pnl"] += adj_pnl
        
        if adj_pnl > 0:
            results["wins"] += 1
        else:
            results["losses"] += 1
        # 记录到分数区间
        score_key = f"{int(score/10)*10}-{int(score/10)*10+9}"
        results["by_score_range"][score_key].append(adj_pnl)



        # 累计每笔的日收益（用于最大回撤计算）
        date = r.get("date", "")
        results.setdefault("_daily_pnls", []).append((date, adj_pnl))

        # TOP盈亏
        results["top_gainers"].append(r)
        results["top_losers"].append(r)

    if results["total_trades"] > 0:
        results["win_rate"] = round(results["wins"] / results["total_trades"] * 100, 1)
        results["avg_pnl_per_trade"] = round(results["total_pnl"] / results["total_trades"], 2)
    
    # ── TS01: 计算最大回撤（用累积收益曲线的最大谷底） ──────
    daily = sorted(results.get("_daily_pnls", []), key=lambda x: x[0])
    if daily:
        cum = 0.0
        max_cum = 0.0
        max_dd = 0.0
        for _, pnl in daily:
            cum += pnl
            max_cum = max(max_cum, cum)
            dd = cum - max_cum  # 负数：从峰顶到谷底的亏损
            max_dd = min(max_dd, dd)
        results["max_drawdown"] = round(max_dd, 2)
    del results["_daily_pnls"]

    # 排序TOP
    results["top_gainers"] = sorted(results["top_gainers"], key=lambda x: x.get("pnl", 0), reverse=True)[:5]
    results["top_losers"] = sorted(results["top_losers"], key=lambda x: x.get("pnl", 0))[:5]

    return results


def compare_strategies(old_result: dict, new_result: dict) -> dict:
    """比较新旧策略"""
    comparison = {
        "old": old_result["strategy"],
        "new": new_result["strategy"],
        "verdict": "PENDING",
        "details": [],
    }
    
    old_wr = old_result["win_rate"]
    new_wr = new_result["win_rate"]
    
    if new_result["total_trades"] < 3:
        comparison["verdict"] = "❌ 样本不足（<3笔交易）"
        comparison["details"].append(f"新策略仅{new_result['total_trades']}笔交易，样本不足")
        return comparison
    
    if new_wr > old_wr + 5:
        comparison["verdict"] = "✅ 胜率提升" 
    elif new_wr >= old_wr - 3:
        comparison["verdict"] = "⚠️ 胜率持平（可接受）"
    else:
        comparison["verdict"] = "❌ 胜率下降"
    
    comparison["details"] = [
        f"旧策略({old_result['strategy']}): {old_result['total_trades']}笔 胜率{old_result['win_rate']}% 均收益{old_result['avg_pnl_per_trade']}%",
        f"新策略({new_result['strategy']}): {new_result['total_trades']}笔 胜率{new_result['win_rate']}% 均收益{new_result['avg_pnl_per_trade']}%",
        f"胜率差: {new_wr - old_wr:+.1f}pp",
        f"均收益差: {new_result['avg_pnl_per_trade'] - old_result['avg_pnl_per_trade']:+.2f}pp",
    ]
    
    return comparison


def print_report(result: dict, label: str = ""):
    """打印回测报告"""
    print(f"\n{'='*50}")
    print(f"📊 回测报告: {result['strategy']} {label}")
    print(f"{'='*50}")
    print(f"  总交易: {result['total_trades']}笔")
    print(f"  盈利: {result['wins']}笔 | 亏损: {result['losses']}笔")
    print(f"  胜率: {result['win_rate']}%")
    print(f"  总收益: {result['total_pnl']:+.2f}%")
    print(f"  均收益: {result['avg_pnl_per_trade']:+.2f}%")
    print(f"  最大回撤: {result['max_drawdown']:+.2f}%")
    
    if result.get("by_score_range"):
        print(f"\n  按评分区间:")
        for rng in sorted(result["by_score_range"].keys()):
            pnls = result["by_score_range"][rng]
            avg = sum(pnls) / len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            print(f"    {rng}分: {len(pnls)}笔 胜率{wins/len(pnls)*100:.0f}% 均收益{avg:+.2f}%")
    
    if result.get("top_gainers"):
        print(f"\n  🏆 TOP盈利:")
        for g in result["top_gainers"]:
            print(f"    ✅ {g.get('name','?')}({g.get('code','?')}) {g.get('pnl',0):+.2f}%")
    
    if result.get("top_losers"):
        print(f"\n  💀 TOP亏损:")
        for l in result["top_losers"]:
            print(f"    ❌ {l.get('name','?')}({l.get('code','?')}) {l.get('pnl',0):+.2f}%")


def save_result(result: dict, filename: str):
    """保存回测结果"""
    path = SANDBOX_DIR / filename
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  💾 已保存: {path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="天枢策略回测沙箱")
    parser.add_argument("--days", type=int, default=60, help="回测天数（默认60）")
    parser.add_argument("--compare", action="store_true", help="新旧策略对比")
    parser.add_argument("--approve", action="store_true", help="确认上线新策略")
    parser.add_argument("--strategy", type=str, default=None, help="自定义策略JSON")
    args = parser.parse_args()

    print(f"🏛️ 天枢权衡 · 策略回测沙箱")
    print(f"{'='*50}")
    print(f"  回测窗口: 最近{args.days}天")
    
    # 加载数据
    records = load_historical_decisions(days=args.days)
    print(f"  加载记录: {len(records)}条")
    
    if not records:
        print("  ❌ 无可用历史数据，请先运行回头看")
        return

    # 默认策略回测
    old_result = backtest(DEFAULT_STRATEGY, records)
    print_report(old_result)
    save_result(old_result, f"backtest_{datetime.now().strftime('%Y%m%d')}_current.json")

    # 对比模式
    if args.compare:
        strategy_config = NEW_STRATEGY
        if args.strategy:
            try:
                custom = json.loads(args.strategy)
                strategy_config.update(custom)
                strategy_config["name"] = custom.get("name", "自定义策略")
            except json.JSONDecodeError as e:
                print(f"  ❌ 自定义策略JSON解析失败: {e}")
                return

        new_result = backtest(strategy_config, records)
        print_report(new_result, label="(新策略)")
        save_result(new_result, f"backtest_{datetime.now().strftime('%Y%m%d')}_new.json")

        comparison = compare_strategies(old_result, new_result)
        print(f"\n{'='*50}")
        print(f"📋 策略对比结论")
        print(f"{'='*50}")
        for d in comparison["details"]:
            print(f"  {d}")
        print(f"\n  裁决: {comparison['verdict']}")

        # 保存对比结果
        comparison_path = SANDBOX_DIR / f"comparison_{datetime.now().strftime('%Y%m%d')}.json"
        comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  💾 对比结果已保存: {comparison_path}")

        # 批准模式
        if args.approve and "✅" in comparison["verdict"]:
            print(f"\n  ✅ 新策略已通过沙箱验证，可以上线！")
            print(f"  执行以下命令应用新策略:")
            print(f"    python scripts/backtest_sandbox.py --strategy '{json.dumps(strategy_config, ensure_ascii=False)}'")
        elif args.approve:
            print(f"\n  ⏸️ 新策略未通过验证，请调整参数后重试")

    print(f"\n{'='*50}")
    print(f"✅ 回测沙箱执行完毕")


if __name__ == "__main__":
    main()