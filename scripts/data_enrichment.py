#!/usr/bin/env python3
"""
数据增强 v3 — 2026-09-09
1. PE裁剪 [1, 200]
2. 龙虎榜特征 (RPT_DAILYBILLBOARD_DETAILSNEW)
3. 北向资金特征 (RPT_MUTUAL_DEAL_HISTORY type=005)
4. 移除冗余特征 (turnover/vol_ma5 与 vol_ratio 100%重复)
"""
import json, urllib.request, urllib.parse, time
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
MODEL_DIR = BASE / "data" / "ml_model"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}
API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def fetch_api(report_name, filter_expr=None, page_size=1000):
    """东财数据中心API，自动分页"""
    all_records = []
    page = 1
    while True:
        params = {
            "reportName": report_name,
            "columns": "ALL",
            "pageNumber": str(page),
            "pageSize": str(page_size),
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
        }
        if filter_expr:
            params["filter"] = filter_expr
        url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers=EM_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            result = data.get("result") or {}
            records = result.get("data", [])
            if not records:
                break
            all_records.extend(records)
            total_pages = result.get("pages", 1)
            print(f"  page {page}/{total_pages}: {len(records)}条")
            if page >= total_pages:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  page {page} error: {e}")
            break
    return all_records


def norm_date(dt_str):
    return dt_str[:10] if dt_str else ""


def main():
    print("=" * 60)
    print("数据增强 v3 — PE裁剪 + 龙虎榜 + 北向资金")
    print("=" * 60)

    ds_path = MODEL_DIR / "dataset_v3.json"
    with open(ds_path) as f:
        v3 = json.load(f)
    recs = v3["records"]
    print(f"\n[现有] {len(recs)}条")

    # 1. PE裁剪
    clipped = 0
    for r in recs:
        pe = r.get("pe")
        if pe is not None and pe != 0:
            orig = pe
            pe = max(1.0, min(200.0, pe))
            if pe != orig:
                clipped += 1
            r["pe"] = round(pe, 2)
    print(f"[PE裁剪] {clipped}个极端值→[1,200]")

    # 2. 龙虎榜
    print("\n[龙虎榜] 获取...")
    bb_raw = fetch_api("RPT_DAILYBILLBOARD_DETAILSNEW")
    print(f"  原始: {len(bb_raw)}条")
    bb_idx = {}
    for item in bb_raw:
        d = norm_date(item.get("TRADE_DATE", ""))
        c = item.get("SECURITY_CODE", "")
        if not d or not c:
            continue
        key = (d, c)
        if key not in bb_idx:
            bb_idx[key] = {
                "net": item.get("BILLBOARD_NET_AMT", 0) or 0,
                "buy": item.get("BILLBOARD_BUY_AMT", 0) or 0,
            }
        else:
            bb_idx[key]["net"] += item.get("BILLBOARD_NET_AMT", 0) or 0
            bb_idx[key]["buy"] += item.get("BILLBOARD_BUY_AMT", 0) or 0
    print(f"  去重: {len(bb_idx)}个(日期,股票)")

    # 3. 北向资金 (type=005 = 北向合计)
    print("\n[北向资金] 获取 (MUTUAL_TYPE=005)...")
    nb_raw = fetch_api("RPT_MUTUAL_DEAL_HISTORY", filter_expr='(MUTUAL_TYPE="005")')
    print(f"  原始: {len(nb_raw)}条")
    nb_idx = {}
    for item in nb_raw:
        d = norm_date(item.get("TRADE_DATE", ""))
        amt = item.get("DEAL_AMT", 0) or 0
        if d and amt:
            nb_idx[d] = amt
    print(f"  覆盖: {len(nb_idx)}天")

    # 4. 添加特征
    bb_match = 0
    nb_match = 0
    for r in recs:
        d, c = r.get("date", ""), r.get("code", "")
        # 龙虎榜
        bb = bb_idx.get((d, c))
        if bb:
            r["billboard_net_amt"] = round(bb["net"], 2)
            r["billboard_buy_amt"] = round(bb["buy"], 2)
            r["is_billboard"] = 1
            bb_match += 1
        else:
            r["billboard_net_amt"] = 0
            r["billboard_buy_amt"] = 0
            r["is_billboard"] = 0
        # 北向
        nb = nb_idx.get(d, 0)
        r["northbound_deal_amt"] = round(nb, 2)
        if nb > 0:
            nb_match += 1

    print(f"\n[特征添加]")
    print(f"  龙虎榜: {bb_match}/{len(recs)} ({bb_match/len(recs)*100:.1f}%)")
    print(f"  北向: {nb_match}/{len(recs)} ({nb_match/len(recs)*100:.1f}%)")

    # 5. 更新特征列表
    old = v3.get("feature_keys", [])
    new = [f for f in old if f not in ("turnover", "vol_ma5")]
    new += ["billboard_net_amt", "billboard_buy_amt", "is_billboard", "northbound_deal_amt"]
    v3["feature_keys"] = new
    v3["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    v3["enrichment"] = {
        "pe_clipped": clipped,
        "billboard_matched": bb_match,
        "northbound_dates": len(nb_idx),
    }

    with open(ds_path, "w") as f:
        json.dump(v3, f, ensure_ascii=False, indent=2)

    print(f"\n[完成] {len(new)}个特征")
    print(f"  新增: billboard_net_amt, billboard_buy_amt, is_billboard, northbound_deal_amt")
    print(f"  移除: turnover, vol_ma5 (与vol_ratio重复)")


if __name__ == "__main__":
    main()
