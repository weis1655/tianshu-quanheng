#!/usr/bin/env python3
"""
天枢权衡全量历史验证 — 所有决策报告的抽样验证
读43天决策报告 → 拉行情 → 算准确率
"""
import json, re, time, urllib.request, sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
HISTORY = BASE / "data" / "历史记录"
OUT = BASE / "data" / "full_sampling_verify.json"
PRICE_CACHE = {}  # {code: {date: price}}

def get_prefix(code):
    c = str(code).strip()
    if c.startswith('6'): return 'sh'
    if c.startswith(('0','3')): return 'sz'
    if c.startswith(('8','4')): return 'bj'
    return 'sh'

def fetch_prices(code, num_days=60):
    if code in PRICE_CACHE:
        return PRICE_CACHE[code]
    prefix = get_prefix(code)
    urls = [
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}",
        f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}"
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"Referer":"https://finance.sina.com.cn/","User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8","replace")
            data = json.loads(raw)
            prices = {d["day"]: float(d["close"]) for d in data if "day" in d and "close" in d}
            PRICE_CACHE[code] = prices
            return prices
        except Exception:
            continue
    PRICE_CACHE[code] = {}
    return {}

def calc_ret(prices, entry_date, hold=3):
    sd = sorted(prices.keys())
    idx = None
    for i, d in enumerate(sd):
        if d >= entry_date:
            idx = i
            break
    if idx is None or idx + hold >= len(sd):
        return None
    entry = prices[sd[idx]]
    exit_p = prices[sd[idx + hold]]
    if entry and entry > 0:
        return round((exit_p - entry) / entry * 100, 2)
    return None

def parse_decision(fp):
    """完整解析决策报告"""
    content = fp.read_text(encoding="utf-8")
    m = re.search(r'(\d{4}-\d{2}-\d{2})', fp.name)
    date = m.group(1) if m else ""

    is_empty = bool(re.search(r'(空仓|暂无.*推荐|空仓等待)', content))

    # 提取所有标的（主推/观察/降级操作/备选）
    stocks = []
    seen = set()
    for type_tag, default_type in [("【主推】", "主推"), ("【观察】", "观察"), ("【降级操作】", "降级操作"), ("【备选", "备选")]:
        for m in re.finditer(rf'{re.escape(type_tag)}[^\]]*\]\s*([\u4e00-\u9fa5]{{2,6}})\s*[（(](\d{{6}})[）)]', content):
            key = (m.group(2), m.group(1))
            if key not in seen:
                seen.add(key)
                stocks.append({"type": default_type, "name": m.group(1), "code": m.group(2)})

    # 兜底：没有标记时匹配「股票名（代码）」格式
    if not stocks and not is_empty:
        for m in re.finditer(r'[（(](\d{6})[）)]', content):
            # 找前面的股票名
            pre = content[max(0, m.start()-20):m.start()]
            nm = re.search(r'([\u4e00-\u9fa5]{2,6})\s*$', pre)
            if nm:
                key = (m.group(1), nm.group(1))
                if key not in seen:
                    seen.add(key)
                    stocks.append({"type": "主推", "name": nm.group(1), "code": m.group(1)})

    return {"date": date, "is_empty": is_empty, "stocks": stocks}

# ── 主流程 ──────────────────────────────────────────
def main():
    # 收集所有决策文件
    files = sorted(HISTORY.glob("*_决策报告.md"))
    print(f"📂 找到 {len(files)} 份决策报告\n")

    all_days = []
    total_picks = 0
    total_profit_3d = 0
    total_profit_5d = 0
    results = []

    for fp in files:
        decision = parse_decision(fp)
        if decision["is_empty"]:
            print(f"  {decision['date']} 📭 空仓")
            all_days.append({"date": decision["date"], "type": "空仓", "stocks": [], "avg_r3": None})
            continue

        day_stocks = []
        for s in decision["stocks"]:
            prices = fetch_prices(s["code"])
            time.sleep(0.25)

            if not prices:
                print(f"  {decision['date']} ⚠️ {s['name']}({s['code']}) 行情失败")
                continue

            r3 = calc_ret(prices, decision["date"], 3)
            r5 = calc_ret(prices, decision["date"], 5)
            r10 = calc_ret(prices, decision["date"], 10)

            is_profit_3d = (r3 or 0) > 0
            total_picks += 1
            if is_profit_3d:
                total_profit_3d += 1
            if r5 and r5 > 0:
                total_profit_5d += 1

            mark = "✅" if is_profit_3d else "❌"
            r3_s = f"{r3:+.2f}%" if r3 else "N/A"
            print(f"  {decision['date']} {mark} {s['name']:6s}({s['code']}) [{s['type']}] 3日:{r3_s}")

            day_stocks.append({
                **s,
                "r3": r3, "r5": r5, "r10": r10,
                "is_profit_3d": is_profit_3d,
            })

        if day_stocks:
            avg_r3 = sum(s["r3"] for s in day_stocks if s["r3"] is not None) / max(1, sum(1 for s in day_stocks if s["r3"] is not None))
            all_days.append({"date": decision["date"], "type": "执行", "stocks": day_stocks, "avg_r3": round(avg_r3, 2)})
        else:
            all_days.append({"date": decision["date"], "type": "空仓(无有效数据)", "stocks": [], "avg_r3": None})

    # 统计
    print("\n" + "="*70)
    print(f" 📊 全量 {len(files)} 天验证统计")
    print("="*70)

    empty_days = sum(1 for d in all_days if d["type"] == "空仓" or d["type"] == "空仓(无有效数据)")
    exec_days = sum(1 for d in all_days if d["type"] == "执行")

    print(f"\n  总交易日: {len(files)}")
    print(f"  空仓天数: {empty_days} ({empty_days/len(files)*100:.0f}%)")
    print(f"  执行天数: {exec_days} ({exec_days/len(files)*100:.0f}%)")
    print(f"  总推荐数: {total_picks}")
    print(f"  3日胜率: {total_profit_3d}/{total_picks} ({total_profit_3d/total_picks*100:.1f}%)" if total_picks > 0 else "  无数据")
    print(f"  5日胜率: {total_profit_5d}/{total_picks} ({total_profit_5d/total_picks*100:.1f}%)" if total_picks > 0 else "  无数据")

    # 按月统计
    monthly = defaultdict(list)
    for d in all_days:
        if d["stocks"]:
            month_key = d["date"][:7]
            for s in d["stocks"]:
                if s.get("r3") is not None:
                    monthly[month_key].append(s)

    print("\n  📅 月度统计:")
    for mk in sorted(monthly.keys()):
        ss = monthly[mk]
        wins = sum(1 for s in ss if s.get("is_profit_3d"))
        avg = sum(s["r3"] for s in ss if s["r3"] is not None) / max(1, len(ss))
        print(f"    {mk}: {len(ss)}只, 盈利{wins}只, 胜率{wins/len(ss)*100:.0f}%, 平均{avg:+.2f}%")

    # 按标的类型统计
    print("\n  📈 按推荐类型:")
    type_stats = defaultdict(list)
    for d in all_days:
        for s in d.get("stocks", []):
            if s.get("r3") is not None:
                type_stats[s["type"]].append(s)
    for tp in sorted(type_stats.keys()):
        ss = type_stats[tp]
        wins = sum(1 for s in ss if s["is_profit_3d"])
        avg = sum(s["r3"] for s in ss if s["r3"] is not None) / len(ss)
        print(f"    {tp}: {len(ss)}只, 胜率{wins/len(ss)*100:.0f}%, 平均{avg:+.2f}%")

    # 最佳/最差
    all_stocks_with_data = []
    for d in all_days:
        for s in d.get("stocks", []):
            if s.get("r3") is not None:
                s["_date"] = d["date"]
                all_stocks_with_data.append(s)
    if all_stocks_with_data:
        best = max(all_stocks_with_data, key=lambda x: x["r3"] or -999)
        worst = min(all_stocks_with_data, key=lambda x: x["r3"] or 999)
        print(f"\n  🏆 最佳: {best['name']}({best['code']}) {best['_date']} +{best['r3']:+.2f}%")
        print(f"  💀 最差: {worst['name']}({worst['code']}) {worst['_date']} {worst['r3']:+.2f}%")

    # 保存
    summary = {
        "total_days": len(files),
        "empty_days": empty_days,
        "exec_days": exec_days,
        "total_picks": total_picks,
        "profit_3d": total_profit_3d,
        "winrate_3d": round(total_profit_3d/total_picks*100, 1) if total_picks else 0,
        "profit_5d": total_profit_5d,
        "winrate_5d": round(total_profit_5d/total_picks*100, 1) if total_picks else 0,
    }
    out_data = {"generated_at": datetime.now().isoformat(), "summary": summary, "days": all_days}
    OUT.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 全量验证完成 → {OUT}")

if __name__ == "__main__":
    main()
