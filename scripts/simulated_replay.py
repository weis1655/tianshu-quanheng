#!/usr/bin/env python3
"""
模拟实盘回放引擎 - 天枢权衡 P-H04 决策-执行断链的过渡期解法
================================================================
不接券商API, 用K线缓存离线回放决策的真实盈亏, 让 decision_log 的
actual_pnl 从 7% 提升到 90%+, 给胜率统计和ML训练提供真实标签。

三层能力(共用本引擎):
  C. 历史回放: 回填历史决策盈亏(entry_price缺失时用次日收盘代理)
  A. 实时闭环: 每日写虚拟持仓 -> T+N算盈亏 -> 回填(由cron调本脚本)
  B. 滑点校准: 引入成交量冲击成本, 模拟真实执行偏差

用法:
  python scripts/simulated_replay.py --mode c      # C: 历史回放回填
  python scripts/simulated_replay.py --mode a      # A: 读今日决策写虚拟持仓+T+N验证
  python scripts/simulated_replay.py --mode b      # B: 滑点校准+净值曲线
  python scripts/simulated_replay.py --mode c --dry-run   # 只预览不写盘
"""
import json
import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
from logger import plog  # noqa: E402

DECISION_LOG = ROOT / "data" / "decision_log.json"
KLINE_CACHE = ROOT / "data" / "ml_model" / "kline_cache.json"
HOLD_POOL = ROOT / "data" / "五池管理" / "持仓池.json"

# T+N 验证节点(交易日)
T_NODES = [3, 5, 10]
# P-H05 滑点/冲击成本(双向, 占成交额比例)
SLIPPAGE_RATE = 0.0002  # 0.02%


def load_json(path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        plog("WARNING", f"[模拟回放] 读{path.name}失败: {e}")
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_klines():
    """K线缓存: {code: [{date,open,close,high,low,volume,amount}, ...]} 按日期升序"""
    kc = load_json(KLINE_CACHE, {}) or {}
    # 按日期升序排序
    for code in list(kc.keys()):
        kl = kc.get(code)
        if isinstance(kl, list):
            kc[code] = sorted(kl, key=lambda x: x.get("date", ""))
    return kc


def build_date_index(klines):
    """构建 {code: {date: kline_dict}} 快速索引"""
    idx = {}
    for code, kl in klines.items():
        idx[code] = {x.get("date"): x for x in kl}
    return idx


def build_trading_days(klines):
    """从K线缓存提取全局交易日序列(升序)"""
    days = set()
    for kl in klines.values():
        for x in kl:
            d = x.get("date")
            if d:
                days.add(d)
    return sorted(days)


def get_close_at_or_after(date_index, code, target_date, trading_days, max_lag=1):
    """取 target_date 当日或之后 max_lag 个交易日的收盘价"""
    klines_by_code = date_index.get(code, {})
    if not klines_by_code:
        return None
    # 找 target_date 在交易日序列中的位置
    if target_date not in trading_days:
        # 定位到最近的不早于target_date的交易日
        pos = None
        for i, d in enumerate(trading_days):
            if d >= target_date:
                pos = i
                break
        if pos is None:
            return None
    else:
        pos = trading_days.index(target_date)
    # 从pos开始向后找有收盘价的
    for lag in range(max_lag + 1):
        d = trading_days[pos + lag] if pos + lag < len(trading_days) else None
        if d is None:
            break
        kl = klines_by_code.get(d)
        if kl and kl.get("close"):
            return kl["close"], d
    return None


def get_close_before(date_index, code, target_date, trading_days, max_lag=1):
    """取 target_date 当日或之前 max_lag 个交易日的收盘价(用于代理入场价)"""
    klines_by_code = date_index.get(code, {})
    if not klines_by_code:
        return None
    if target_date not in trading_days:
        pos = None
        for i, d in enumerate(trading_days):
            if d <= target_date:
                pos = i
        if pos is None:
            return None
    else:
        pos = trading_days.index(target_date)
    for lag in range(max_lag + 1):
        idx = pos - lag
        if idx < 0:
            break
        d = trading_days[idx]
        kl = klines_by_code.get(d)
        if kl and kl.get("close"):
            return kl["close"], d
    return None


def calc_pnl(entry_price, exit_price, slippage=SLIPPAGE_RATE):
    """计算盈亏百分比, 含滑点/冲击成本(P-H05)"""
    if not entry_price or not exit_price:
        return None
    # 买入付滑点, 卖出付滑点 -> 有效入场价上浮, 有效出场价下浮
    eff_entry = entry_price * (1 + slippage)
    eff_exit = exit_price * (1 - slippage)
    return round((eff_exit - eff_entry) / eff_entry * 100, 2)


# ─────────────────────────────────────────────────────────────────
# 模式 C: 历史回放回填
# ─────────────────────────────────────────────────────────────────
def replay_history(dry_run=False):
    """
    对 decision_log 所有决策做离线回放:
    - entry_price 已有 -> 直接用
    - entry_price 缺失 -> 用决策日次日前收盘价作代理入场价(标记 proxy=True)
    - 计算 T+3/T+5/T+10 收益率, 回填 actual_pnl(T+10为主)
    """
    recs = load_json(DECISION_LOG, [])
    if isinstance(recs, dict):
        recs = recs.get("decision_records", [])
    if not recs:
        plog("WARNING", "[模拟回放C] decision_log 为空, 无可回放记录")
        return
    klines = load_klines()
    date_idx = build_date_index(klines)
    trading_days = build_trading_days(klines)

    filled = 0
    proxy_filled = 0
    skipped = 0
    results = []

    for r in recs:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code", ""))
        date = r.get("date", "")
        entry_price = r.get("entry_price")
        if not code or not date or len(code) != 6:
            skipped += 1
            continue
        # 已有非零actual_pnl且非代理 -> 跳过(保留盟主手动回填的真数据)
        if (r.get("actual_pnl") is not None and r.get("actual_pnl") != 0
                and not r.get("$proxy_pnl")):
            continue

        # 入场价: 优先用已有entry_price, 否则用次日前收盘代理
        proxy = False
        if isinstance(entry_price, (int, float)) and entry_price > 0:
            ep = entry_price
            ep_date = date
        else:
            prev = get_close_before(date_idx, code, date, trading_days, max_lag=1)
            if prev:
                ep, ep_date = prev
                proxy = True
            else:
                skipped += 1
                continue

        # T+N 出场价
        t_results = {}
        for n in T_NODES:
            exit_price = get_close_at_or_after(date_idx, code, _add_trading_days(trading_days, date, n), trading_days)
            if exit_price:
                t_results[f"T+{n}"] = {
                    "price": exit_price[0],
                    "date": exit_price[1],
                    "pnl": calc_pnl(ep, exit_price[0]),
                }
        if not t_results:
            skipped += 1
            continue

        # 主回填: T+10 优先, 否则 T+5, 否则 T+3
        main_node = "T+10" if "T+10" in t_results else ("T+5" if "T+5" in t_results else "T+3")
        main_pnl = t_results[main_node]["pnl"]

        r["actual_pnl"] = main_pnl
        r["actual_change"] = f"{main_pnl:+.2f}%"
        r["entry_price"] = round(ep, 3)
        r["entry_date"] = ep_date
        r["exit_node"] = main_node
        r["exit_date"] = t_results[main_node]["date"]
        r["exit_price"] = round(t_results[main_node]["price"], 3)
        r["t_returns"] = {k: v["pnl"] for k, v in t_results.items()}
        r["$proxy_pnl"] = proxy  # 标记是否代理入场价(非真实决策买入价)
        r["verify_date"] = datetime.now().strftime("%Y-%m-%d")

        if proxy:
            proxy_filled += 1
        else:
            filled += 1
        results.append({"code": code, "date": date, "entry": ep, "proxy": proxy,
                        "T+3": t_results.get("T+3", {}).get("pnl"),
                        "T+5": t_results.get("T+5", {}).get("pnl"),
                        "T+10": t_results.get("T+10", {}).get("pnl")})

    if not dry_run:
        save_json(DECISION_LOG, recs)

    # 统计
    plog("INFO", f"[模拟回放C] {'预览' if dry_run else '完成'}: "
                 f"真实入场价回填{filled}条, 代理入场价回填{proxy_filled}条, 跳过{skipped}条")
    # 胜率统计
    valid_pnl = [r["actual_pnl"] for r in recs if isinstance(r.get("actual_pnl"), (int, float))]
    if valid_pnl:
        wins = sum(1 for p in valid_pnl if p > 0)
        plog("INFO", f"[模拟回放C] 有盈亏记录: {len(valid_pnl)}条, "
                     f"胜率: {wins}/{len(valid_pnl)} ({wins/len(valid_pnl)*100:.1f}%), "
                     f"平均盈亏: {sum(valid_pnl)/len(valid_pnl):+.2f}%")
        # 按T节点分别统计
        for node in ["T+3", "T+5", "T+10"]:
            pnls = [r.get("t_returns", {}).get(node) for r in recs
                    if isinstance(r.get("t_returns", {}).get(node), (int, float))]
            if pnls:
                w = sum(1 for p in pnls if p > 0)
                plog("INFO", f"[模拟回放C] {node}: {len(pnls)}条, 胜率{w/len(pnls)*100:.1f}%, "
                             f"均值{sum(pnls)/len(pnls):+.2f}%")
    return results


def _add_trading_days(trading_days, start_date, n):
    """从 start_date 起第 n 个交易日"""
    if start_date not in trading_days:
        for i, d in enumerate(trading_days):
            if d >= start_date:
                return trading_days[min(i + n, len(trading_days) - 1)]
        return None
    idx = trading_days.index(start_date)
    return trading_days[min(idx + n, len(trading_days) - 1)]


# ─────────────────────────────────────────────────────────────────
# 模式 A: 实时闭环 - 写虚拟持仓 + T+N 验证回填
# ─────────────────────────────────────────────────────────────────
def daily_cycle(dry_run=False):
    """
    每日cron调用:
    1. 读今日决策报告, 把【主推】标的写入持仓池(虚拟持仓, 真实入场价)
    2. 对持仓池已过T+N节点的标的算盈亏回填 decision_log
    """
    today = datetime.now().strftime("%Y-%m-%d")
    klines = load_klines()
    date_idx = build_date_index(klines)
    trading_days = build_trading_days(klines)

    # 1. 读今日决策报告, 提取【主推】标的
    decision_report = ROOT / "data" / "历史记录" / f"{today}_决策报告.md"
    new_holdings = []
    if decision_report.exists():
        import re
        txt = decision_report.read_text(encoding="utf-8", errors="ignore")
        # 宽松匹配【主推】(与pool_updater一致)
        for m in re.finditer(r"【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", txt):
            name, code = m.group(1).strip(), m.group(2).strip()
            # 提取买入价
            buy_m = re.search(rf"{re.escape(name)}[^$]*?买入价[：:]\s*([\d.]+)", txt)
            buy_price = float(buy_m.group(1)) if buy_m else None
            if buy_price:
                new_holdings.append({
                    "代码": code, "名称": name,
                    "入场价": buy_price, "入场日期": today,
                    "买入价来源": "决策报告",
                    "状态": "持仓中",
                    "T+节点待验证": T_NODES,
                })
        if new_holdings:
            plog("INFO", f"[模拟回放A] 今日决策报告提取{len(new_holdings)}只主推标写入虚拟持仓")

    # 2. 读持仓池, 合并
    pool = load_json(HOLD_POOL, {"stocks": []})
    if not isinstance(pool, dict):
        pool = {"stocks": []}
    existing_codes = {str(s.get("代码", "")) for s in pool.get("stocks", [])}
    for h in new_holdings:
        if h["代码"] not in existing_codes:
            pool["stocks"].append(h)
            existing_codes.add(h["代码"])

    # 3. 对持仓池已过T+N的标的算盈亏
    recs = load_json(DECISION_LOG, [])
    if isinstance(recs, dict):
        recs = recs.get("decision_records", [])
    verified = 0
    for s in pool.get("stocks", []):
        entry_date = s.get("入场日期", "")
        entry_price = s.get("入场价")
        code = str(s.get("代码", ""))
        if not entry_date or not isinstance(entry_price, (int, float)) or not entry_price:
            continue
        # 找该持仓对应的decision_log记录
        log_rec = next((r for r in recs if str(r.get("code")) == code and r.get("date") == entry_date), None)
        for n in T_NODES:
            target = _add_trading_days(trading_days, entry_date, n)
            if target and target <= today:
                exit_price = get_close_at_or_after(date_idx, code, target, trading_days)
                if exit_price:
                    pnl = calc_pnl(entry_price, exit_price[0])
                    node_key = f"T+{n}"
                    if log_rec:
                        log_rec.setdefault("t_returns", {})[node_key] = pnl
                        # 主节点回填
                        if n == 10 or (n == 5 and 10 > n):
                            log_rec["actual_pnl"] = pnl
                            log_rec["actual_change"] = f"{pnl:+.2f}%"
                        verified += 1
                    s["状态"] = "已验证"
                    break

    if not dry_run:
        save_json(HOLD_POOL, pool)
        save_json(DECISION_LOG, recs)
    plog("INFO", f"[模拟回放A] {'预览' if dry_run else '完成'}: "
                 f"新虚拟持仓{len(new_holdings)}只, 验证回填{verified}条, 持仓池现有{len(pool.get('stocks', []))}只")


# ─────────────────────────────────────────────────────────────────
# 模式 B: 滑点校准 + 净值曲线
# ─────────────────────────────────────────────────────────────────
def net_value_curve(initial_capital=100000, max_position_pct=10.0, max_concurrent=3):
    """
    基于已回填的 decision_log 盈亏, 模拟净值曲线:
    - 初始资金10万, 单票最大仓位10%(对齐portfolio_manager.max_position_pct),
      同时最多持仓3只(对齐thresholds.POOL_CAPACITY_S_POOL=3)
    - 按决策日顺序, 模拟建仓->T+10平仓
    - 含P-H05滑点冲击成本
    - 注: 早期B用30%单仓位顺序累加模型, 产生65.98%伪回撤(模型假设148笔顺序满仓),
      现改为并发持仓模型(同时3只×10%仓位), 反映真实风险暴露
    """
    recs = load_json(DECISION_LOG, [])
    if isinstance(recs, dict):
        recs = recs.get("decision_records", [])
    # 有完整盈亏记录的
    trades = [r for r in recs
              if isinstance(r.get("actual_pnl"), (int, float)) and r.get("exit_date") and r.get("entry_date")]
    trades.sort(key=lambda x: x.get("entry_date", ""))
    if not trades:
        plog("WARNING", "[模拟回放B] 无完整盈亏记录, 无法生成净值曲线")
        return

    equity = initial_capital
    peak = initial_capital
    max_drawdown = 0
    max_dd_date = ""
    curve = []
    # 并发持仓: 维护当前持仓集合 {entry_key: trade}
    # entry_key = (code, entry_date) 区分同一股票不同日期的决策
    open_positions = {}
    processed = set()  # {(code, entry_date)} 已建仓的决策

    all_dates = sorted(set(t["entry_date"] for t in trades) | set(t["exit_date"] for t in trades))
    for date in all_dates:
        # 1. 当日新建仓: 若并发持仓未达上限, 建仓
        for t in trades:
            entry_key = (t["code"], t["entry_date"])
            if t["entry_date"] == date and entry_key not in processed:
                if len(open_positions) < max_concurrent:
                    pos_value = equity * max_position_pct / 100.0
                    pnl_pct = t["actual_pnl"]
                    gain = pos_value * pnl_pct / 100.0
                    open_positions[entry_key] = {"trade": t, "pos_value": pos_value, "gain": gain}
                    processed.add(entry_key)
                # 超过上限的新决策: 不建仓(反映真实容量约束)

        # 2. 当日平仓: exit_date == date 的持仓实现盈亏
        for entry_key in list(open_positions.keys()):
            t = open_positions[entry_key]["trade"]
            if t["exit_date"] == date:
                equity += open_positions[entry_key]["gain"]
                del open_positions[entry_key]

        # 3. 记录净值曲线
        if not curve or equity != curve[-1][1]:
            curve.append((date, equity))

        # 4. 更新峰值和回撤
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd
            max_dd_date = date

    final = equity
    total_return = (final - initial_capital) / initial_capital * 100
    plog("INFO", f"[模拟回放B] 净值曲线: 初始{initial_capital:.0f} -> 终值{final:.0f} "
                 f"({total_return:+.2f}%), 最大回撤{max_drawdown:.2f}%"
                 f"({max_dd_date}), 交易{len(processed)}/{len(trades)}笔执行"
                 f"(单票{max_position_pct}%仓位, 并发上限{max_concurrent}只)")
    # 写净值曲线
    nv_file = ROOT / "data" / "模拟净值曲线.json"
    save_json(nv_file, {
        "initial_capital": initial_capital,
        "final_capital": round(final, 2),
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "max_drawdown_date": max_dd_date,
        "trade_count": len(trades),
        "executed_count": len(processed),
        "skipped_count": len(trades) - len(processed),
        "curve": [{"date": d, "equity": round(e, 2)} for d, e in curve],
        "note": f"含P-H05滑点成本, 单票{max_position_pct}%仓位, 并发上限{max_concurrent}只, 基于T+10节点盈亏",
    })
    plog("INFO", f"[模拟回放B] 净值曲线已写入 {nv_file.name}")


def main():
    parser = argparse.ArgumentParser(description="模拟实盘回放引擎")
    parser.add_argument("--mode", choices=["c", "a", "b"], default="c",
                        help="c=历史回放回填, a=实时闭环, b=滑点校准+净值曲线")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写盘")
    args = parser.parse_args()

    if args.mode == "c":
        replay_history(dry_run=args.dry_run)
    elif args.mode == "a":
        daily_cycle(dry_run=args.dry_run)
    elif args.mode == "b":
        net_value_curve()


if __name__ == "__main__":
    main()
