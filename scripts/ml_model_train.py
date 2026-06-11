#!/usr/bin/env python3
"""
ML评分模型 v3 — 训练随机森林回归模型
用6因子+评分预测3日收益率, 对比校验 LLM 评分有效性
"""
import json, sys
import numpy as np
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA_FILE = BASE / "data" / "ml_model" / "dataset_v2.json"

# ── 加载数据 ─────────────────────────────────────────────

def load_dataset():
    with open(DATA_FILE) as f:
        raw = json.load(f)["records"]
    
    # 只保留有 r3 和 特征的数据
    records = []
    for r in raw:
        if r["r3"] is None:
            continue
        if not any(r.get(k) for k in ("ma5_div", "ma10_div", "ret5", "ret20", "vol_ratio")):
            continue
        records.append(r)
    return records

def prepare_features(records):
    """构建特征矩阵和标签"""
    features = []
    labels = []
    labels_win = []
    
    FEATURE_KEYS = [
        "score",        # LLM评分
        "ma5_div",      # 5日乖离
        "ma10_div",     # 10日乖离
        "ret5",         # 5日涨幅
        "ret20",        # 20日涨幅
        "vol20",        # 20日波动率
        "vol_ratio",    # 量比
        "day_range",    # 振幅
        "ma20_pos",     # 相对20日线位置
    ]
    
    for r in records:
        row = []
        for k in FEATURE_KEYS:
            row.append(float(r.get(k, 0) or 0))
        features.append(row)
        labels.append(r["r3"])
        labels_win.append(1 if r["r3"] > 0 else 0)
    
    return np.array(features), np.array(labels), np.array(labels_win), FEATURE_KEYS

# ── 训练 ─────────────────────────────────────────────────

def train():
    print("=" * 60)
    print("ML评分模型 v3 — 随机森林训练")
    print("=" * 60)
    
    records = load_dataset()
    print(f"📊 加载数据: {len(records)} 条有效记录")
    
    X, y, y_win, feature_names = prepare_features(records)
    print(f"📊 特征维度: {X.shape[1]} ({', '.join(feature_names)})")
    
    # 基础统计
    print(f"\n📊 标签分布 (r3):")
    print(f"   均值: {y.mean():.2f}%")
    print(f"   中位数: {np.median(y):.2f}%")
    print(f"   标准差: {y.std():.2f}%")
    print(f"   胜率(r3>0): {y_win.sum()}/{len(y_win)} = {y_win.mean()*100:.1f}%")
    
    # ── 训练随机森林 ─────────────────────────────────
    from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
    from sklearn.model_selection import cross_val_score, KFold, train_test_split
    from sklearn.metrics import mean_squared_error, r2_score, accuracy_score
    import warnings
    warnings.filterwarnings('ignore')
    
    # 回归模型
    rf_reg = RandomForestRegressor(
        n_estimators=200,
        max_depth=5,
        min_samples_leaf=5,
        random_state=42,
    )
    
    # 分类模型（预测涨/跌）
    rf_clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=5,
        min_samples_leaf=5,
        random_state=42,
    )
    
    # 5折交叉验证
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    
    # 回归CV
    cv_r2 = cross_val_score(rf_reg, X, y, cv=kf, scoring='r2')
    cv_mse = cross_val_score(rf_reg, X, y, cv=kf, scoring='neg_mean_squared_error')
    print(f"\n📊 5折交叉验证 (回归):")
    print(f"   R²: {cv_r2.mean():.3f} (±{cv_r2.std():.3f})")
    print(f"   MSE: {-cv_mse.mean():.3f} (±{cv_mse.std():.3f})")
    
    # 分类CV
    cv_acc = cross_val_score(rf_clf, X, y_win, cv=kf, scoring='accuracy')
    print(f"\n📊 5折交叉验证 (分类-涨跌):")
    print(f"   准确率: {cv_acc.mean()*100:.1f}% (±{cv_acc.std()*100:.1f}%)")
    
    # ── 全量训练 ────────────────────────────────────
    rf_reg.fit(X, y)
    rf_clf.fit(X, y_win)
    
    # 特征重要性
    print(f"\n📊 特征重要性 (回归模型):")
    importances = sorted(zip(feature_names, rf_reg.feature_importances_), 
                         key=lambda x: x[1], reverse=True)
    for name, imp in importances:
        bar = "█" * int(imp * 50)
        print(f"   {name:<12} {imp:.3f} {bar}")
    
    print(f"\n📊 特征重要性 (分类模型):")
    importances_clf = sorted(zip(feature_names, rf_clf.feature_importances_),
                             key=lambda x: x[1], reverse=True)
    for name, imp in importances_clf:
        bar = "█" * int(imp * 50)
        print(f"   {name:<12} {imp:.3f} {bar}")
    
    # ── 对比LLM评分 vs ML模型 ──────────────────────
    # 用当前LLM评分做阈值决策
    print(f"\n📊 LLM评分 vs ML模型 对比:")
    print(f"   {'指标':<20} {'LLM评分(≥75)':<20} {'ML分类':<20} {'ML回归(>0)':<20}")
    
    # LLM: 按score ≥75 选
    llm_pred = (X[:, 0] >= 75).astype(int)
    llm_correct = (llm_pred == y_win)
    llm_acc = llm_correct.mean()
    
    # ML分类
    ml_clf_pred = rf_clf.predict(X)
    ml_clf_acc = (ml_clf_pred == y_win).mean()
    
    # ML回归: 预测>0 代表看涨
    ml_reg_pred = (rf_reg.predict(X) > 0).astype(int)
    ml_reg_acc = (ml_reg_pred == y_win).mean()
    
    print(f"   {'准确率':<20} {llm_acc*100:<20.1f}% {ml_clf_acc*100:<20.1f}% {ml_reg_acc*100:<20.1f}%")
    
    # 精确率/召回率
    def calc_precision_recall(pred, actual):
        tp = ((pred == 1) & (actual == 1)).sum()
        fp = ((pred == 1) & (actual == 0)).sum()
        fn = ((pred == 0) & (actual == 1)).sum()
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        return precision, recall
    
    llm_prec, llm_recall = calc_precision_recall(llm_pred, y_win)
    ml_clf_prec, ml_clf_recall = calc_precision_recall(ml_clf_pred, y_win)
    ml_reg_prec, ml_reg_recall = calc_precision_recall(ml_reg_pred, y_win)
    
    print(f"   {'精确率(涨预测)':<20} {llm_prec*100:<20.1f}% {ml_clf_prec*100:<20.1f}% {ml_reg_prec*100:<20.1f}%")
    print(f"   {'召回率(涨预测)':<20} {llm_recall*100:<20.1f}% {ml_clf_recall*100:<20.1f}% {ml_reg_recall*100:<20.1f}%")
    
    # ── 按分数段精确度 ──
    print(f"\n📊 按LLM评分分段的绝对预测误差:")
    buckets = defaultdict(list)
    for i in range(len(records)):
        score_bucket = int(X[i, 0]) // 10 * 10
        buckets[score_bucket].append((y[i], rf_reg.predict([X[i]])[0]))
    for bucket in sorted(buckets.keys()):
        items = buckets[bucket]
        actuals = [a for a, _ in items]
        preds = [p for _, p in items]
        mae = np.mean([abs(a - p) for a, p in items])
        actual_mean = np.mean(actuals)
        pred_mean = np.mean(preds)
        print(f"   {bucket}-{bucket+9}分: {len(items)}条 实际均值{actual_mean:.2f}% 预测均值{pred_mean:.2f}% MAE{mae:.2f}%")
    
    # ── 保存模型 ──
    import joblib
    model_dir = BASE / "data" / "ml_model"
    model_dir.mkdir(parents=True, exist_ok=True)
    
    joblib.dump(rf_reg, model_dir / "rf_regressor.pkl")
    joblib.dump(rf_clf, model_dir / "rf_classifier.pkl")
    
    # 保存模型元数据
    meta = {
        "model": "RandomForest",
        "feature_names": feature_names,
        "n_records": len(records),
        "cv_r2_mean": float(cv_r2.mean()),
        "cv_r2_std": float(cv_r2.std()),
        "cv_acc_mean": float(cv_acc.mean()),
        "cv_acc_std": float(cv_acc.std()),
        "llm_score_accuracy": float(llm_acc),
        "ml_classifier_accuracy": float(ml_clf_acc),
        "ml_regressor_accuracy": float(ml_reg_acc),
        "feature_importance_reg": {n: float(i) for n, i in importances},
        "feature_importance_clf": {n: float(i) for n, i in importances_clf},
    }
    with open(model_dir / "model_metadata.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 模型已保存:")
    print(f"   回归器: {model_dir / 'rf_regressor.pkl'}")
    print(f"   分类器: {model_dir / 'rf_classifier.pkl'}")
    print(f"   元数据: {model_dir / 'model_metadata.json'}")

if __name__ == "__main__":
    train()