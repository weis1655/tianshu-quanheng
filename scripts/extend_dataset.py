#!/usr/bin/env python3
"""
ML 训练数据扩展 v2 — 2026-09-09
目标：310条 → 500+条，13特征 → 16特征，加市场状态标签

三步走：
1. 从缓存 K 线批量生成记录（idx>=20且r3可用）
2. 腾讯行情 API 拉基本面（PE/PB/市值）
3. 上证指数计算市场状态（牛/熊/震荡）
"""
import json, time, sys, math
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request

BASE = Path(__file__).parent.parent
MODEL_DIR = BASE / "data" / "ml_model"
CACHE_FILE = MODEL_DIR / "kline_cache.json"
MARKET_CACHE = MODEL_DIR / "market_klines.json"

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# ── 目标特征列表（16个） ──
FEATURE_KEYS = [
    "score", "ma5_div", "ma10_div", "ret5", "ret20",
    "vol20", "vol_ratio", "day_range", "ma20_pos",
    "bias_5", "bias_20", "turnover", "amplitude",
    "gap_up", "vol_ma5", "ma20_slope", "ret5_annual",
    "pe", "pb", "mktcap_rank",  # 基本面
    "market_state",  # 市场状态: 0=熊 1=震荡 2=牛
]


def tx_kline(code, beg="20260101", end="20260910"):
    """腾讯K线"""
    tx = f"sh{code}" if code.startswith(("60", "68")) else f"sz{code}"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx},day,,,500,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": EM_HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode())
        data = d.get("data", {})
        inner = data.get(tx, {}) if isinstance(data, dict) else {}
        kl = (inner.get("qfqday", []) or inner.get("day", [])) if isinstance(inner, dict) else []
        beg_dt = datetime.strptime(beg, "%Y%m%d")
        end_dt = datetime.strptime(end, "%Y%m%d")
        return [
            {"date": k[0], "open": float(k[1]), "close": float(k[2]),
             "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]), "amount": 0}
            for k in kl if beg_dt <= datetime.strptime(k[0], "%Y-%m-%d") <= end_dt
        ]
    except:
        return []


def em_kline(code, beg="20260101", end="20260910"):
    """东财K线"""
    market = 1 if code.startswith(("60", "68")) else 0
    url = (f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt=1&beg={beg}&end={end}&lmt=500")
    try:
        req = urllib.request.Request(url, headers=EM_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode())
        return [
            {"date": p[0], "open": float(p[1]), "close": float(p[2]),
             "high": float(p[3]), "low": float(p[4]), "volume": float(p[5]), "amount": float(p[6])}
            for k in d.get("data", {}).get("klines", []) if (p := k.split(","))
        ]
    except:
        return []


def fetch_kline(code, beg="20260101", end="20260910"):
    r = em_kline(code, beg, end)
    return r if r else tx_kline(code, beg, end)


def tx_fundamental(code):
    """腾讯行情API获取基本面（PE/PB/市值）"""
    tx = f"sh{code}" if code.startswith(("60", "68")) else f"sz{code}"
    url = f"http://qt.gtimg.cn/q={tx}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": EM_HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("gbk")
        parts = text.split("~")
        pe = float(parts[39]) if len(parts) > 39 and parts[39] else 0
        pb = float(parts[46]) if len(parts) > 46 and parts[46] else 0
        mktcap = float(parts[45]) if len(parts) > 45 and parts[45] else 0
        return pe, pb, mktcap
    except:
        return 0, 0, 0


def get_market_klines():
    """获取上证指数K线"""
    if MARKET_CACHE.exists():
        with open(MARKET_CACHE) as f:
            cached = json.load(f)
        # 如果缓存有9月数据，直接用
        if cached and cached[-1]["date"] >= "2026-09-01":
            return cached
    kl = em_kline("000001", "20260101", "20260910")
    if not kl:
        kl = tx_kline("000001", "20260101", "20260910")
    if kl:
        with open(MARKET_CACHE, "w") as f:
            json.dump(kl, f)
    return kl


def compute_market_state(market_klines, target_date):
    """
    计算市场状态标签
    0=熊市(MA20下行+价格在MA20下方)
    1=震荡(MA20方向不明确或价格在MA20附近)
    2=牛市(MA20上行+价格在MA20上方)
    """
    if not market_klines:
        return 1  # 默认震荡
    # 找到目标日期或之前最近的数据
    closes = [(k["date"], k["close"]) for k in market_klines]
    target_idx = -1
    for i, (d, _) in enumerate(closes):
        if d <= target_date:
            target_idx = i
        else:
            break
    if target_idx < 20:
        return 1
    # MA20 和 MA20 3天前
    cur_ma20 = float(np.mean([closes[j][1] for j in range(target_idx - 19, target_idx + 1)]))
    prev_ma20 = float(np.mean([closes[j][1] for j in range(target_idx - 22, target_idx - 2)]))
    cur_close = closes[target_idx][1]
    ma20_change = (cur_ma20 - prev_ma20) / prev_ma20 * 100
    price_vs_ma20 = (cur_close - cur_ma20) / cur_ma20 * 100
    # 判定
    if ma20_change > 0.3 and price_vs_ma20 > 0:
        return 2  # 牛市
    elif ma20_change < -0.3 and price_vs_ma20 < 0:
        return 0  # 熊市
    else:
        return 1  # 震荡


def compute_features_from_klines(klines, target_date, pe=0, pb=0, mktcap=0, mktcap_rank=0, market_state=1):
    """计算全部特征"""
    if not klines or target_date not in [k["date"] for k in klines]:
        return None
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    if idx < 20 or idx + 3 >= len(klines):
        return None

    cl = np.array([k["close"] for k in klines[:idx + 1]])
    vol = np.array([k["volume"] for k in klines[:idx + 1]])
    hi = np.array([k["high"] for k in klines[:idx + 1]])
    lo = np.array([k["low"] for k in klines[:idx + 1]])
    op = np.array([k["open"] for k in klines[:idx + 1]])

    cc = float(cl[-1])
    m5 = float(np.mean(cl[-5:]))
    m10 = float(np.mean(cl[-10:]))
    m20 = float(np.mean(cl[-20:]))
    rets = np.diff(cl[-21:]) / cl[-21:-1]
    v20 = float(np.std(rets[-20:])) if len(rets) >= 20 else 0

    return {
        # 原始8特征
        "ma5_div": round((cc - m5) / m5 * 100, 2),
        "ma10_div": round((cc - m10) / m10 * 100, 2),
        "ret5": round(float((cl[-1] - cl[-6]) / cl[-6] * 100), 2) if idx >= 5 else 0,
        "ret20": round(float((cl[-1] - cl[-21]) / cl[-21] * 100), 2) if idx >= 20 else 0,
        "vol20": round(v20 * 100, 2),
        "vol_ratio": round(float(np.mean(vol[-5:]) / np.mean(vol[-20:])), 2) if np.mean(vol[-20:]) > 0 else 0,
        "day_range": round(float((hi[-1] - lo[-1]) / cc * 100), 2),
        "ma20_pos": round((cc - m20) / m20 * 100, 2),
        # 扩展特征
        "bias_5": round((cc - m5) / m5 * 100, 2),
        "bias_20": round((cc - m20) / m20 * 100, 2),
        "turnover": round(float(np.mean(vol[-5:]) / np.mean(vol[-20:])), 2) if np.mean(vol[-20:]) > 0 else 0,
        "amplitude": round(float(np.mean((hi[-20:] - lo[-20:]) / cl[-20:] * 100)), 2),
        "gap_up": round(float((op[-1] - cl[-2]) / cl[-2] * 100), 2) if idx >= 1 else 0,
        "vol_ma5": round(float(np.mean(vol[-5:]) / np.mean(vol[-20:])), 2) if np.mean(vol[-20:]) > 0 else 0,
        "ma20_slope": round((m20 - float(np.mean(cl[-23:-3]))) / float(np.mean(cl[-23:-3])) * 100, 2) if idx >= 23 else 0,
        "ret5_annual": round(float(np.std(np.diff(cl[-6:]) / cl[-6:-1]) * math.sqrt(252) * 100), 2) if idx >= 5 else 0,
        # 基本面
        "pe": pe,
        "pb": pb,
        "mktcap_rank": mktcap_rank,
        # 市场状态
        "market_state": market_state,
        # 辅助
        "close_price": cc,
    }


def compute_returns(klines, target_date):
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    cur = klines[idx]["close"]
    def safe(d):
        return round((klines[idx + d]["close"] - cur) / cur * 100, 2) if idx + d < len(klines) else None
    return {"r3": safe(3), "r5": safe(5), "r10": safe(10)}


def main():
    print("=" * 60)
    print("ML 训练数据扩展 v2 — 500+目标")
    print("=" * 60)

    # 1. 加载现有数据
    with open(MODEL_DIR / "dataset_v3.json") as f:
        v3 = json.load(f)
    existing_keys = set((r["code"], r["date"]) for r in v3["records"])
    print(f"\n[现有] {len(v3['records'])} 条")

    # 2. 加载 K 线缓存
    with open(CACHE_FILE) as f:
        cache = json.load(f)
    print(f"[K线缓存] {len(cache)} 只")

    # 3. 获取上证指数
    market_kl = get_market_klines()
    print(f"[上证指数] {len(market_kl)} 天")

    # 4. 获取基本面（并发）
    print("\n[基本面] 腾讯行情API...")
    all_codes = set(cache.keys())
    all_codes.discard("N/A")
    fundamentals = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(tx_fundamental, c): c for c in sorted(all_codes)}
        for fut in as_completed(futs):
            c = futs[fut]
            pe, pb, mktcap = fut.result()
            if pe > 0 or pb > 0:
                fundamentals[c] = {"pe": pe, "pb": pb, "mktcap": mktcap}
    print(f"  获取成功: {len(fundamentals)} 只")

    # 计算市值排名百分位
    mktcaps = sorted(fundamentals[c]["mktcap"] for c in fundamentals)
    def mktcap_rank(mktcap):
        if mktcap == 0 or not mktcaps:
            return 0.5
        for i, mc in enumerate(mktcaps):
            if mc >= mktcap:
                return i / len(mktcaps)
        return 1.0

    # 5. 从缓存K线批量生成记录
    print(f"\n[生成记录] 从 {len(cache)} 只股票K线中...")
    new_records = []
    for code, kl in sorted(cache.items()):
        if not kl or len(kl) < 24:
            continue
        fund = fundamentals.get(code, {"pe": 0, "pb": 0, "mktcap": 0})
        pe, pb, mktcap = fund["pe"], fund["pb"], fund["mktcap"]
        rank = mktcap_rank(mktcap)

        # 遍历每个可用交易日
        for idx in range(20, len(kl) - 3):
            date = kl[idx]["date"]
            if (code, date) in existing_keys:
                continue
            # 计算市场状态
            mstate = compute_market_state(market_kl, date)
            # 计算特征
            feats = compute_features_from_klines(kl, date, pe, pb, mktcap, rank, mstate)
            if not feats:
                continue
            returns = compute_returns(kl, date)
            # 默认score=50（非候选池日期没有LLM评分）
            rec = {"code": code, "name": "", "date": date, "score": 50, "is_main": False, **feats, **returns}
            new_records.append(rec)

    print(f"  新增记录: {len(new_records)} 条")
    print(f"  总计: {len(v3['records']) + len(new_records)} 条")

    # 6. 合并保存
    v3["records"].extend(new_records)
    v3["total_entries"] = len(v3["records"])
    v3["records_with_r3"] = sum(1 for r in v3["records"] if r.get("r3") is not None)
    v3["records_with_r5"] = sum(1 for r in v3["records"] if r.get("r5") is not None)
    v3["feature_keys"] = FEATURE_KEYS
    v3["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(MODEL_DIR / "dataset_v3.json", "w") as f:
        json.dump(v3, f, ensure_ascii=False, indent=2)

    # 保存基本面缓存
    with open(MODEL_DIR / "fundamentals.json", "w") as f:
        json.dump(fundamentals, f, ensure_ascii=False, indent=2)

    # 7. 统计
    has_r3 = v3["records_with_r3"]
    print(f"\n[最终]")
    print(f"  总记录: {len(v3['records'])}")
    print(f"  有r3: {has_r3}")
    print(f"  特征数: {len(FEATURE_KEYS)}")
    print(f"  基本面: {len(fundamentals)} 只股票有PE/PB")

    # 市场状态分布
    states = {}
    for r in v3["records"]:
        s = r.get("market_state", 1)
        states[s] = states.get(s, 0) + 1
    labels = {0: "熊市", 1: "震荡", 2: "牛市"}
    for s, c in sorted(states.items()):
        print(f"  市场状态 {labels.get(s,s)}: {c} ({c/len(v3['records'])*100:.1f}%)")


if __name__ == "__main__":
    main()
