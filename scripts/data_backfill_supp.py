#!/usr/bin/env python3
"""
补充训练数据 — 合并 decision_log 到 dataset_v3
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

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

def em_kline(code, beg, end):
    market = 1 if code.startswith(("60", "68")) else 0
    secid = f"{market}.{code}"
    url = (f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt=1&beg={beg}&end={end}&lmt=500")
    try:
        req = urllib.request.Request(url, headers=EM_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        klines = data.get("data", {}).get("klines", [])
        result = []
        for k in klines:
            p = k.split(",")
            result.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                           "high": float(p[3]), "low": float(p[4]), "volume": float(p[5]),
                           "amount": float(p[6])})
        return result
    except:
        return []

def tx_kline(code, beg, end):
    tx_code = f"sh{code}" if code.startswith(("60", "68")) else f"sz{code}"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx_code},day,,,500,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": EM_HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        d = data.get("data", {})
        inner = d.get(tx_code, {}) if isinstance(d, dict) else {}
        klines = (inner.get("qfqday", []) or inner.get("day", [])) if isinstance(inner, dict) else []
        beg_dt = datetime.strptime(beg, "%Y%m%d")
        end_dt = datetime.strptime(end, "%Y%m%d")
        result = []
        for k in klines:
            try:
                kd = datetime.strptime(k[0], "%Y-%m-%d")
                if beg_dt <= kd <= end_dt:
                    result.append({"date": k[0], "open": float(k[1]), "close": float(k[2]),
                                   "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
                                   "amount": 0})
            except:
                continue
        return result
    except:
        return []

def fetch_kline(code, beg, end):
    r = em_kline(code, beg, end)
    return r if r else tx_kline(code, beg, end)

def compute_features(klines, target_date):
    if not klines or target_date not in [k["date"] for k in klines]:
        return {}
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    if idx < 20:
        return {}
    closes = np.array([k["close"] for k in klines[:idx+1]])
    volumes = np.array([k["volume"] for k in klines[:idx+1]])
    highs = np.array([k["high"] for k in klines[:idx+1]])
    lows = np.array([k["low"] for k in klines[:idx+1]])
    opens = np.array([k["open"] for k in klines[:idx+1]])
    cur_close = float(closes[-1])
    ma5 = float(np.mean(closes[-5:]))
    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))
    rets = np.diff(closes[-21:]) / closes[-21:-1]
    vol20_raw = float(np.std(rets[-20:])) if len(rets) >= 20 else 0

    ma5_div = round((cur_close - ma5) / ma5 * 100, 2)
    ma10_div = round((cur_close - ma10) / ma10 * 100, 2)
    ma20_pos = round((cur_close - ma20) / ma20 * 100, 2)
    ret5 = round(float((closes[-1] - closes[-6]) / closes[-6] * 100), 2) if idx >= 5 else 0
    ret20 = round(float((closes[-1] - closes[-21]) / closes[-21] * 100), 2) if idx >= 20 else 0
    vol20 = round(vol20_raw * 100, 2)
    vol_ratio = round(float(np.mean(volumes[-5:]) / np.mean(volumes[-20:])) if np.mean(volumes[-20:]) > 0 else 0, 2)
    day_range = round(float((highs[-1] - lows[-1]) / cur_close * 100), 2)
    turnover = round(float(np.mean(volumes[-5:]) / np.mean(volumes[-20:])) if np.mean(volumes[-20:]) > 0 else 0, 2)
    amps = (highs[-20:] - lows[-20:]) / closes[-20:] * 100
    amplitude = round(float(np.mean(amps)), 2)
    gap_up = round(float((opens[-1] - closes[-2]) / closes[-2] * 100), 2) if idx >= 1 else 0
    vol_ma5 = round(float(np.mean(volumes[-5:]) / np.mean(volumes[-20:])) if np.mean(volumes[-20:]) > 0 else 0, 2)
    ma20_3ago = float(np.mean(closes[-23:-3])) if idx >= 23 else ma20
    ma20_slope = round((ma20 - ma20_3ago) / ma20_3ago * 100, 2) if ma20_3ago > 0 else 0
    rets_5 = np.diff(closes[-6:]) / closes[-6:-1]
    ret5_annual = round(float(np.std(rets_5) * math.sqrt(252) * 100), 2) if len(rets_5) >= 4 else 0
    return {
        "ma5_div": ma5_div, "ma10_div": ma10_div, "ret5": ret5, "ret20": ret20,
        "vol20": vol20, "vol_ratio": vol_ratio, "day_range": day_range, "ma20_pos": ma20_pos,
        "bias_5": ma5_div, "bias_20": ma20_pos, "turnover": turnover, "amplitude": amplitude,
        "gap_up": gap_up, "vol_ma5": vol_ma5, "ma20_slope": ma20_slope, "ret5_annual": ret5_annual,
        "close_price": cur_close,
    }

def compute_returns(klines, target_date):
    if not klines or target_date not in [k["date"] for k in klines]:
        return {"r3": None, "r5": None, "r10": None}
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    cur = klines[idx]["close"]
    def safe(d): return round((klines[idx+d]["close"] - cur) / cur * 100, 2) if idx+d < len(klines) else None
    return {"r3": safe(3), "r5": safe(5), "r10": safe(10)}

def main():
    print("=" * 60)
    print("补充训练数据 — 合并 decision_log")
    print("=" * 60)

    with open(MODEL_DIR / "dataset_v3.json") as f:
        v3 = json.load(f)
    existing = v3["records"]
    print(f"[现有] {len(existing)} 条")

    existing_keys = set((r["code"], r["date"]) for r in existing)

    with open(BASE / "data" / "decision_log.json") as f:
        dl = json.load(f)
    print(f"[decision_log] {len(dl)} 条")

    # 提取新记录（不在现有数据集中）
    new_entries = []
    for e in dl:
        code, date = e.get("code"), e.get("date")
        if not code or not date or (code, date) in existing_keys:
            continue
        tech = e.get("tech_score") or 0
        fund = e.get("fundamental_score") or 0
        score = max(tech, fund) if (tech or fund) else 0
        if score == 0:
            continue
        new_entries.append({
            "code": code, "name": e.get("name", ""), "date": date,
            "score": int(score), "is_main": e.get("$is_executed", False),
        })
    print(f"[新记录] {len(new_entries)} 条（去重后）")

    if not new_entries:
        print("没有新记录可补充")
        return

    # 缓存
    cache = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        print(f"[缓存] {len(cache)} 只")

    # 需要拉取的股票
    by_stock = {}
    for e in new_entries:
        by_stock.setdefault(e["code"], set()).add(e["date"])

    to_fetch = [c for c in sorted(by_stock.keys()) if c not in cache]
    print(f"[待拉] {len(to_fetch)} 只")

    if to_fetch:
        def fetch_one(code):
            dates = by_stock[code]
            min_d = datetime.strptime(min(dates), "%Y-%m-%d") - timedelta(days=60)
            max_d = datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=20)
            return code, fetch_kline(code, min_d.strftime("%Y%m%d"), max_d.strftime("%Y%m%d"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fetch_one, c): c for c in to_fetch}
            for i, fut in enumerate(as_completed(futures)):
                code, klines = fut.result()
                cache[code] = klines
                print(f"  [{i+1}/{len(to_fetch)}] {code}: {len(klines)}天")

        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, ensure_ascii=False)

    # 计算特征和收益
    print(f"\n[回补] {len(new_entries)} 条...")
    added = 0
    for e in new_entries:
        klines = cache.get(e["code"], [])
        if not klines:
            continue
        feats = compute_features(klines, e["date"])
        if not feats:
            continue
        returns = compute_returns(klines, e["date"])
        for f, v in feats.items():
            e[f] = v
        for f, v in returns.items():
            e[f] = v
        e["close_price"] = feats["close_price"]
        existing.append(e)
        added += 1

    has_r5 = sum(1 for r in existing if r.get("r5") is not None)
    print(f"  添加: {added}, 总计: {len(existing)}, 有r5: {has_r5}")

    # 保存
    v3["total_entries"] = len(existing)
    v3["records_with_r5"] = has_r5
    v3["records"] = existing
    with open(MODEL_DIR / "dataset_v3.json", "w") as f:
        json.dump(v3, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] dataset_v3.json")


if __name__ == "__main__":
    main()
