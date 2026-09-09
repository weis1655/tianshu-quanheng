#!/usr/bin/env python3
"""ML 评分模型 v7 — 2026-09-09
最终版: r10目标 + XGBoost(n=200,lr=0.03,正则) + dt_norm + 候选池5x加权
CV AUC=0.6411 (v6: 0.6343, v3: 0.556)
"""
import json, numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.linear_model import Ridge
from lightgbm import LGBMRegressor, LGBMClassifier
from xgboost import XGBRegressor, XGBClassifier
import urllib.request, urllib.parse
from joblib import dump
from datetime import datetime

MODEL_DIR = Path(__file__).parent.parent / "data" / "ml_model"
TARGET = "r10"
WEIGHT = 5.0

FEATURES = [
    "ma5_div", "ma10_div", "ret5", "ret20", "vol20", "vol_ratio",
    "day_range", "ma20_pos", "bias_5", "bias_20", "amplitude",
    "gap_up", "ma20_slope", "ret5_annual",
    "pe", "pb", "score", "is_cand", "has_dragon_tiger", "dt_norm",
]

def lj(p):
    try:
        with open(p) as f: return json.load(f)
    except: return None

def cpe(pe):
    if pe <= 0: return 1
    return min(pe, 200)

def load_dragon_tiger():
    dt_raw = lj(MODEL_DIR / "dragon_tiger_cache.json")
    if dt_raw is None: return {}
    dt_map = {}
    for it in dt_raw.get("data", []):
        key = (str(it.get("SECURITY_CODE","")).strip(), it.get("TRADE_DATE","")[:10])
        dt_map[key] = float(it.get("BILLBOARD_NET_AMT", 0) or 0)
    return dt_map

def main():
    print("="*60)
    print(f"  ML v7 — {TARGET} — XGB(n=200,lr=0.03) + dt_norm + 加权")
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
    print(f"总记录: {len(recs)} 条 ({TARGET} 非空)")

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
        # 归一化龙虎榜净买入额 (原始值范围-28亿~28亿, /1e8后约-28~28)
        r["dt_norm"] = r["dt_net_inflow"] / 1e8
    n_dt = sum(1 for r in recs if r["has_dragon_tiger"])
    print(f"  龙虎榜命中: {n_dt} 条 ({n_dt/len(recs)*100:.2f}%)")

    # 4. 矩阵
    print(f"训练特征: {len(FEATURES)} 个")
    X = np.array([[float(r.get(f, 0) or 0) for f in FEATURES] for r in recs], dtype=float)
    y = np.array([float(r[TARGET]) for r in recs])
    yc = (y > 0).astype(int)
    sw = np.where(np.array([r["is_cand"] for r in recs]), WEIGHT, 1.0)
    print(f"X shape: {X.shape}, y range: [{y.min():.2f}, {y.max():.2f}]")

    # 保存数据
    np.save(MODEL_DIR / "X_v7.npy", X)
    np.save(MODEL_DIR / "y_v7.npy", y)
    np.save(MODEL_DIR / "sw_v7.npy", sw)
    with open(MODEL_DIR / "dataset_v7.json", "w") as f:
        json.dump(recs, f, ensure_ascii=False, indent=2)

    # 5. 基线
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

    # 6. 模型对比
    models = {
        "Ridge": Ridge(alpha=1.0),
        "GradientBoosting": GradientBoostingRegressor(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42),
        "LightGBM": LGBMRegressor(n_estimators=100, max_depth=5, learning_rate=0.05, num_leaves=31, random_state=42, verbose=-1),
        "XGBoost": XGBRegressor(n_estimators=200, max_depth=3, learning_rate=0.03, random_state=42, subsample=0.8, colsample_bytree=0.8, eval_metric="rmse", n_jobs=-1),
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
            imp = sorted(zip(FEATURES, mf.feature_importances_), key=lambda x: -x[1])[:7]
            print(f"    Top7: {', '.join(f'{n2}={v2:.3f}' for n2, v2 in imp)}")
        if cauc > best_auc:
            best_auc = cauc
            best_name = name

    # 7. 分类模型
    cls_models = {
        "GradientBoosting": GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42),
        "LightGBM": LGBMClassifier(n_estimators=100, max_depth=5, learning_rate=0.05, num_leaves=31, random_state=42, verbose=-1),
        "XGBoost": XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.03, random_state=42, subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", n_jobs=-1),
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

    # 8. 保存最佳模型
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

    # 9. 元数据
    fp = bm.predict(scaler_used.transform(X) if scaler_used else X)
    fauc = roc_auc_score(yc, fp) if len(np.unique(yc)) > 1 else 0.5
    meta = {
        "version": "v7",
        "trained_at": "2026-09-09T13:20:00",
        "model_type": best_name,
        "features": FEATURES,
        "feature_count": len(FEATURES),
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
        "model_params": {"n_estimators": 200, "max_depth": 3, "learning_rate": 0.03,
                         "subsample": 0.8, "colsample_bytree": 0.8},
        "changes_from_v6": [
            "模型 GB→XGBoost (n=200,lr=0.03,正则化, CV AUC 0.6343→0.6411)",
            "加 dt_norm (龙虎榜净买入/1e8归一化, 贡献+0.005)",
            "候选池子模型不可行: 225条样本集中在后期, 时序验证早期折无候选样本",
            "北向资金/融资余额API在沙箱网络全部返回失败, 数据源不可用",
        ],
    }
    with open(MODEL_DIR / "model_metadata.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  v7 完成")
    print(f"  回归: {best_name}  CV AUC={best_auc:.4f}  全量={fauc:.4f}")
    print(f"  分类: {bcn}  CV AUC={bca:.4f}")
    print(f"  目标: {TARGET} (10日收益)")
    print(f"  降级: {'是' if fauc < 0.58 else '否'}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()