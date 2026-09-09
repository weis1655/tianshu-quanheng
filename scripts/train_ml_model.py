#!/usr/bin/env python3
"""
ML 评分模型重训 v3 — 2026-09-09
改进：
1. 使用 dataset_v3.json（187 条，17 特征，全有 r5/r10）
2. Ridge 回归 + GB 分类（小样本友好）
3. TimeSeriesSplit 交叉验证（时间顺序，防泄漏）
4. 特征选择：剔除冗余（bias_5==ma5_div, bias_20==ma20_pos, turnover≈vol_ratio, vol_ma5≈vol_ratio）
5. 输出模型 + 元数据 + 降级建议
"""
import json, sys
import numpy as np
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
MODEL_DIR = BASE / "data" / "ml_model"

# 17 个特征（已去掉冗余：bias_5/bias_20 与 ma5_div/ma20_pos 重复，turnover/vol_ma5 与 vol_ratio 重复）
FEATURE_KEYS = [
    "score", "ma5_div", "ma10_div", "ret5", "ret20",
    "vol20", "vol_ratio", "day_range", "ma20_pos",
    "amplitude", "gap_up", "ma20_slope", "ret5_annual"
]
# 保留的 13 个独立特征 + score
TARGET = "r5"

def load_dataset():
    with open(MODEL_DIR / "dataset_v3.json") as f:
        data = json.load(f)
    records = data["records"]
    print(f"[原始] {len(records)} 条")

    # 过滤 r5 缺失 + 全零
    records = [r for r in records if r.get(TARGET) is not None]
    records = [r for r in records if any(
        r.get(f, 0) != 0 for f in FEATURE_KEYS[1:]
    )]
    print(f"[过滤] {len(records)} 条有效记录")

    # 构建矩阵
    X = np.array([[r.get(f, 0) or 0 for f in FEATURE_KEYS] for r in records])
    y_reg = np.array([r[TARGET] for r in records])
    y_clf = (y_reg > 0).astype(int)

    # 去极端值（3σ 裁剪）
    print("\n[去极端值]")
    for i, f in enumerate(FEATURE_KEYS):
        vals = X[:, i]
        mean, std = np.mean(vals), np.std(vals)
        if std > 0:
            lo, hi = mean - 3 * std, mean + 3 * std
            clipped = np.sum((vals < lo) | (vals > hi))
            if clipped > 0:
                print(f"  {f}: 裁剪 {clipped} 个 [{lo:.2f}, {hi:.2f}]")
            X[:, i] = np.clip(vals, lo, hi)
    # 裁剪目标
    y_mean, y_std = np.mean(y_reg), np.std(y_reg)
    if y_std > 0:
        lo, hi = y_mean - 3 * y_std, y_mean + 3 * y_std
        y_reg = np.clip(y_reg, lo, hi)
        print(f"  {TARGET}: 裁剪到 [{lo:.2f}, {hi:.2f}]")

    print(f"\n[训练集] {len(X)} 条 × {len(FEATURE_KEYS)} 特征")
    print(f"  上涨: {y_clf.sum()} ({y_clf.mean()*100:.1f}%)")
    print(f"  平均收益: {y_reg.mean():.2f}%")
    print(f"  收益率范围: [{y_reg.min():.2f}%, {y_reg.max():.2f}%]")

    # 记录日期用于时序划分
    dates = np.array([r["date"] for r in records])
    return X, y_reg, y_clf, dates


def train_and_evaluate(X, y_reg, y_clf, dates):
    from sklearn.model_selection import TimeSeriesSplit, KFold, cross_val_score
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import r2_score, roc_auc_score, mean_absolute_error
    import warnings
    warnings.filterwarnings("ignore")

    # 标准化（对 Ridge 重要）
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # ── 时间序列划分 ──
    # 按日期排序
    order = np.argsort(dates)
    X_ts, y_reg_ts, y_clf_ts = X[order], y_reg[order], y_clf[order]
    X_scaled_ts = X_scaled[order]

    print("\n" + "=" * 50)
    print("时序交叉验证 (TimeSeriesSplit, 5折)")
    print("=" * 50)

    tscv = TimeSeriesSplit(n_splits=5)

    models = {
        "Ridge": ("reg", Ridge(alpha=1.0)),
        "Ridge-5": ("reg", Ridge(alpha=5.0)),
        "GB": ("reg", GradientBoostingRegressor(n_estimators=100, max_depth=3, learning_rate=0.05, subsample=0.8)),
    }

    results = {}
    for name, (typ, model) in models.items():
        if typ == "reg":
            cv_scores = cross_val_score(model, X_scaled_ts if "Ridge" in name else X_ts,
                                        y_reg_ts, cv=tscv, scoring="r2")
            cv_mae = cross_val_score(model, X_scaled_ts if "Ridge" in name else X_ts,
                                     y_reg_ts, cv=tscv, scoring="neg_mean_absolute_error")
            print(f"\n  {name}:")
            print(f"    R²:  {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")
            print(f"    MAE: {-cv_mae.mean():.2f}% ± {-cv_mae.std():.2f}%")
            results[name] = {"r2_mean": cv_scores.mean(), "r2_std": cv_scores.std(),
                            "mae": -cv_mae.mean()}

    # 分类模型
    print(f"\n  分类模型 (r5 > 0):")
    for name, model in [("GB-Clf", GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.05, subsample=0.8))]:
        cv_auc = cross_val_score(model, X_ts, y_clf_ts, cv=tscv, scoring="roc_auc")
        print(f"    {name} AUC: {cv_auc.mean():.3f} ± {cv_auc.std():.3f}")
        results[name] = {"auc_mean": cv_auc.mean(), "auc_std": cv_auc.std()}

    # 基线：预测平均收益率
    baseline_r2 = cross_val_score(
        Ridge(alpha=1e10), X_scaled_ts, y_reg_ts, cv=tscv, scoring="r2"
    ).mean()
    print(f"\n  基线（预测均值）R²: {baseline_r2:.3f}")

    # ── 选择最优模型 ──
    best_reg_name = max(results, key=lambda k: results[k]["r2_mean"] if "r2_mean" in results[k] else -999)
    print(f"\n[选择] 最优回归: {best_reg_name} (R²={results[best_reg_name]['r2_mean']:.3f})")

    # 最终训练：用全部数据
    if "Ridge" in best_reg_name:
        final_reg = Ridge(alpha=float(best_reg_name.split("-")[1]))
        final_reg.fit(X_scaled, y_reg)
        model_type = "ridge"
    elif best_reg_name == "GB":
        final_reg = GradientBoostingRegressor(n_estimators=100, max_depth=3, learning_rate=0.05, subsample=0.8)
        final_reg.fit(X, y_reg)
        model_type = "gb"
    else:
        final_reg = Ridge(alpha=1.0)
        final_reg.fit(X_scaled, y_reg)
        model_type = "ridge"

    final_clf = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.05, subsample=0.8)
    final_clf.fit(X, y_clf)

    # 全量评分
    if model_type == "ridge":
        pred_reg = final_reg.predict(X_scaled)
    else:
        pred_reg = final_reg.predict(X)
    pred_clf = final_clf.predict_proba(X)[:, 1]
    full_r2 = r2_score(y_reg, pred_reg)
    full_auc = roc_auc_score(y_clf, pred_clf)
    print(f"\n[全量] R²={full_r2:.3f}, AUC={full_auc:.3f}")

    # 特征重要性
    if hasattr(final_reg, "feature_importances_"):
        imp_reg = dict(zip(FEATURE_KEYS, final_reg.feature_importances_))
    else:
        # Ridge 用系数绝对值作为重要性
        importances = np.abs(final_reg.coef_)
        imp_reg = dict(zip(FEATURE_KEYS, importances / importances.sum()))

    imp_clf = dict(zip(FEATURE_KEYS, final_clf.feature_importances_))

    return final_reg, final_clf, results, imp_reg, imp_clf, scaler, baseline_r2, full_r2, full_auc


def save_all(final_reg, final_clf, results, imp_reg, imp_clf, scaler, baseline_r2, full_r2, full_auc):
    import joblib
    import pickle

    # 保存模型（统一用 RF 接口兼容现有 ml_scorer.py）
    joblib.dump(final_reg, MODEL_DIR / "rf_regressor.pkl")
    joblib.dump(final_clf, MODEL_DIR / "rf_classifier.pkl")

    # 保存 scaler
    if hasattr(final_reg, "coef_") and not hasattr(final_reg, "feature_importances_"):
        joblib.dump(scaler, MODEL_DIR / "scaler.pkl")
        model_type = "ridge"
    else:
        model_type = "gb"

    # 元数据
    meta = {
        "model_type": model_type,
        "feature_names": FEATURE_KEYS,
        "n_features": len(FEATURE_KEYS),
        "n_records": int(full_r2 >= 0),
        "cv_results": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in results.items()},
        "baseline_r2": round(baseline_r2, 4),
        "full_r2": round(full_r2, 4),
        "full_auc": round(full_auc, 4),
        "feature_importance_reg": {k: round(float(v), 4) for k, v in sorted(imp_reg.items(), key=lambda x: -x[1])},
        "feature_importance_clf": {k: round(float(v), 4) for k, v in sorted(imp_clf.items(), key=lambda x: -x[1])},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sklearn_version": __import__("sklearn").__version__,
    }
    with open(MODEL_DIR / "model_metadata.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] 模型 + 元数据 → {MODEL_DIR}")


if __name__ == "__main__":
    print("=" * 60)
    print("ML 评分模型重训 v3 — Ridge/GB + 时序验证")
    print("=" * 60)

    X, y_reg, y_clf, dates = load_dataset()

    if len(X) < 50:
        print("❌ 训练样本不足 50 条，退出")
        sys.exit(1)

    final_reg, final_clf, results, imp_reg, imp_clf, scaler, baseline_r2, full_r2, full_auc = \
        train_and_evaluate(X, y_reg, y_clf, dates)

    save_all(final_reg, final_clf, results, imp_reg, imp_clf, scaler, baseline_r2, full_r2, full_auc)

    # 降级判断
    print("\n" + "=" * 50)
    print("降级判断")
    print("=" * 50)
    best_auc = max(v.get("auc_mean", 0) for k, v in results.items() if "auc_mean" in v)
    if best_auc < 0.58:
        print(f"⚠️  AUC={best_auc:.3f} < 0.58 → 建议降级为纯 LLM 评分")
    elif best_auc < 0.65:
        print(f"⚠️  AUC={best_auc:.3f} < 0.65 → 模型有参考价值，但需配合 LLM 使用")
    else:
        print(f"✅  AUC={best_auc:.3f} ≥ 0.65 → 模型可独立使用")
