#!/usr/bin/env python3
"""ML 评分模型 v5 — 2026-09-09
修复+新增:
  - PE裁剪 [1,200]
  - 移除冗余特征 (turnover/vol_ma5 与 vol_ratio 100%重复)
  - 候选池标记 (is_main or score>50) + 5x加权
  - 龙虎榜特征 (has_dragon_tiger)
  - LightGBM + 分类模型
  - TimeSeriesSplit 5折时序验证
"""
import json, numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.linear_model import Ridge
from lightgbm import LGBMRegressor, LGBMClassifier
import urllib.request, urllib.parse
from joblib import dump

MODEL_DIR = Path(__file__).parent.parent / "data" / "ml_model"
TARGET = "r3"
WEIGHT = 5.0

# 使用 dataset_v3 自带特征，排除冗余和无效特征
FEATURES = [
    "ma5_div", "ma10_div", "ret5", "ret20", "vol20", "vol_ratio",
    "day_range", "ma20_pos", "bias_5", "bias_20", "amplitude",
    "gap_up", "ma20_slope", "ret5_annual",
]

def lj(p):
    try:
        with open(p) as f: return json.load(f)
    except: return None

def cpe(pe):
    if pe <= 0: return 1
    return min(pe, 200)

def load_dragon_tiger():
    """加载龙虎榜缓存，返回 {(code, date): net_inflow}"""
    dt_raw = lj(MODEL_DIR / "dragon_tiger_cache.json")
    if dt_raw is None:
        # 尝试拉取
        all_items = []
        page = 1
        while page <= 50:
            u = f'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=SECURITY_CODE,TRADE_DATE,BILLBOARD_NET_AMT&filter=&pageNumber={page}&pageSize=500&sortColumns=TRADE_DATE&sortTypes=-1'
            d = json.loads(urllib.request.urlopen(u, timeout=15).read())
            items = (d.get("result") or {}).get("data") or []
            if not items: break
            all_items.extend(items)
            page += 1
        dt_map = {}
        for it in all_items:
            key = (str(it.get("SECURITY_CODE","")).strip(), it.get("TRADE_DATE","")[:10])
            dt_map[key] = float(it.get("BILLBOARD_NET_AMT", 0) or 0)
        return dt_map
    else:
        dt_map = {}
        for it in dt_raw.get("data", []):
            key = (str(it.get("SECURITY_CODE","")).strip(), it.get("TRADE_DATE","")[:10])
            dt_map[key] = float(it.get("BILLBOARD_NET_AMT", 0) or 0)
        return dt_map

def main():
    print("="*60)
    print(f"  ML v5 — {TARGET} — 加权 + LightGBM + 龙虎榜")
    print("="*60)

    # 1. 加载数据
    v3 = lj(MODEL_DIR / "dataset_v3.json")
    if isinstance(v3, dict):
        recs = v3.get("records", [])
    elif isinstance(v3, list):
        recs = v3
    else:
        recs = []
    recs = [r for r in recs if isinstance(r, dict) and r.get(TARGET) is not None]
    print(f"总记录: {len(recs)} 条")

    # 2. 标记候选池 + PE裁剪
    for r in recs:
        r["is_cand"] = 1 if (r.get("is_main") or r.get("score", 0) > 50) else 0
        if "pe" in r: r["pe"] = cpe(float(r["pe"]))
    n_cand = sum(1 for r in recs if r["is_cand"])
    print(f"候选池: {n_cand} 条 ({n_cand/len(recs)*100:.1f}%)")
    pos_rate = sum(1 for r in recs if r[TARGET] > 0) / len(recs)
    print(f"正样本率: {pos_rate:.2%}")

    # 3. 龙虎榜
    print("加载龙虎榜...")
    dt_map = load_dragon_tiger()
    for r in recs:
        key = (str(r.get("code","")).strip(), r.get("date",""))
        r["has_dragon_tiger"] = 1 if key in dt_map else 0
        r["dt_net_inflow"] = dt_map.get(key, 0)
    n_dt = sum(1 for r in recs if r["has_dragon_tiger"])
    print(f"  龙虎榜记录: {n_dt} 条 ({n_dt/len(recs)*100:.2f}%)")

    # 4. 合并特征
    feats = FEATURES + ["pe", "pb", "score", "is_cand", "has_dragon_tiger", "dt_net_inflow"]
    print(f"训练特征: {len(feats)} 个")
    for i, f in enumerate(feats):
        vals = [r.get(f, 0) or 0 for r in recs]
        uniq = len(set(vals))
        print(f"  {f:20s}: {len(vals):6d} vals, {uniq:6d} unique, min={min(vals):10.2f}, max={max(vals):10.2f}")

    # 5. 矩阵
    X = np.array([[float(r.get(f, 0) or 0) for f in feats] for r in recs], dtype=float)
    y = np.array([float(r[TARGET]) for r in recs])
    yc = (y > 0).astype(int)
    sw = np.where(np.array([r["is_cand"] for r in recs]), WEIGHT, 1.0)
    print(f"\nX shape: {X.shape}, y range: [{y.min():.2f}, {y.max():.2f}]")

    # 保存
    np.save(MODEL_DIR / "X_v5.npy", X)
    np.save(MODEL_DIR / "y_v5.npy", y)
    np.save(MODEL_DIR / "sw_v5.npy", sw)
    with open(MODEL_DIR / "dataset_v5.json", "w") as f:
        json.dump(recs, f, ensure_ascii=False, indent=2)

    # 6. 基线
    tscv = TimeSeriesSplit(n_splits=5)
    br2s, baucs = [], []
    for tr, te in tscv.split(X):
        sc = StandardScaler()
        br = Ridge(alpha=1.0)
        br.fit(sc.fit_transform(X[tr]), y[tr])
        p = br.predict(sc.transform(X[te]))
        br2s.append(r2_score(y[te], p))
        if len(np.unique(yc[te])) > 1:
            baucs.append(roc_auc_score(yc[te], p))
    print(f"\n--- 基线 ---")
    print(f"  Ridge CV R2={np.mean(br2s):.4f}  AUC={np.mean(baucs):.4f}")

    # 7. 模型对比
    models = {
        "Ridge": Ridge(alpha=1.0),
        "GradientBoosting": GradientBoostingRegressor(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42),
        "LightGBM": LGBMRegressor(n_estimators=100, max_depth=5, learning_rate=0.05, num_leaves=31, random_state=42, verbose=-1),
    }
    best_name, best_auc = None, 0
    for name, model in models.items():
        cv_r2s, cv_aucs = [], []
        for tr, te in tscv.split(X):
            m = model.__class__(**model.get_params())
            if name == "Ridge":
                sc = StandardScaler()
                m.fit(sc.fit_transform(X[tr]), y[tr])
                p = m.predict(sc.transform(X[te]))
            else:
                m.fit(X[tr], y[tr], sample_weight=sw[tr])
                p = m.predict(X[te])
            cv_r2s.append(r2_score(y[te], p))
            if len(np.unique(yc[te])) > 1:
                cv_aucs.append(roc_auc_score(yc[te], p))
        cr2, cauc = np.mean(cv_r2s), np.mean(cv_aucs)
        mf = model.__class__(**model.get_params())
        if name == "Ridge":
            sf = StandardScaler(); sf.fit(X)
            mf.fit(sf.transform(X), y)
            fp = mf.predict(sf.transform(X))
        else:
            mf.fit(X, y, sample_weight=sw)
            fp = mf.predict(X)
        fr2 = r2_score(y, fp)
        fauc = roc_auc_score(yc, fp) if len(np.unique(yc)) > 1 else 0.5
        print(f"  {name:20s}: CV R2={cr2:.4f} AUC={cauc:.4f} | Full R2={fr2:.4f} AUC={fauc:.4f}")
        if hasattr(mf, "feature_importances_") and mf.feature_importances_ is not None:
            imp = sorted(zip(feats, mf.feature_importances_), key=lambda x: -x[1])[:7]
            print(f"    Top7: {', '.join(f'{n2}={v2:.3f}' for n2, v2 in imp)}")
        if cauc > best_auc:
            best_auc = cauc
            best_name = name

    # 8. 分类模型
    cls_models = {
        "GradientBoosting": GradientBoostingClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42),
        "LightGBM": LGBMClassifier(n_estimators=50, max_depth=5, learning_rate=0.05, num_leaves=31, random_state=42, verbose=-1),
    }
    bcn, bca = None, 0
    for name, cm in cls_models.items():
        ca = []
        for tr, te in tscv.split(X):
            c = cm.__class__(**cm.get_params())
            c.fit(X[tr], yc[tr], sample_weight=sw[tr])
            pp = c.predict_proba(X[te])[:, 1]
            if len(np.unique(yc[te])) > 1:
                ca.append(roc_auc_score(yc[te], pp))
        c_auc = np.mean(ca)
        print(f"  分类 {name:20s}: CV AUC={c_auc:.4f}")
        if c_auc > bca:
            bca = c_auc
            bcn = name

    # 9. 保存最佳模型
    bm = models[best_name]
    scaler_used = None
    if best_name == "Ridge":
        scaler_used = StandardScaler(); scaler_used.fit(X)
        bm.fit(scaler_used.transform(X), y)
        dump(scaler_used, MODEL_DIR / "scaler.joblib")
    else:
        bm.fit(X, y, sample_weight=sw)
    dump(bm, MODEL_DIR / f"model_{best_name}.joblib")
    bcm = cls_models[bcn]
    bcm.fit(X, yc, sample_weight=sw)
    dump(bcm, MODEL_DIR / f"model_cls_{bcn}.joblib")

    # 10. 元数据
    fp = bm.predict(scaler_used.transform(X) if scaler_used else X)
    fauc = roc_auc_score(yc, fp) if len(np.unique(yc)) > 1 else 0.5
    meta = {
        "version": "v5",
        "trained_at": "2026-09-09T10:00:00",
        "model_type": best_name,
        "features": feats,
        "feature_count": len(feats),
        "target": TARGET,
        "n_records": len(recs),
        "n_candidates": n_cand,
        "cv_auc": round(best_auc, 4),
        "full_r2": round(float(r2_score(y, fp)), 4),
        "full_auc": round(float(fauc), 4),
        "cls_cv_auc": round(bca, 4),
        "cls_model": bcn,
        "auto_degrade": fauc < 0.58,
        "sample_weight": WEIGHT,
        "new_features": ["has_dragon_tiger", "dt_net_inflow", "is_cand"],
        "removed_features": ["vol_ma5", "turnover"],
        "fixes": [
            "PE clip [1,200]",
            "dataset_v3 dict structure handled",
            "is_main + score>50 candidate marking",
            "Dragon tiger data from RPT_DAILYBILLBOARD_DETAILSNEW",
        ],
    }
    with open(MODEL_DIR / "model_metadata.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  v5 完成")
    print(f"  回归: {best_name}  CV AUC={best_auc:.4f}  全量={fauc:.4f}")
    print(f"  分类: {bcn}  CV AUC={bca:.4f}")
    print(f"  降级: {'是' if fauc < 0.58 else '否'}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
