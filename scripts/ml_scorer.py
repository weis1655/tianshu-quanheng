#!/usr/bin/env python3
"""
ML评分模型 — 推理接口 v3
2026-09-09：支持 Ridge + GB 双模型类型，17特征，自动降级
"""
import json, sys
import numpy as np
from pathlib import Path

BASE = Path(__file__).parent.parent
MODEL_DIR = BASE / "data" / "ml_model"

# 13 个独立特征（去掉冗余的 bias_5/bias_20/turnover/vol_ma5）
FEATURE_KEYS = [
    "score", "ma5_div", "ma10_div", "ret5", "ret20",
    "vol20", "vol_ratio", "day_range", "ma20_pos",
    "amplitude", "gap_up", "ma20_slope", "ret5_annual"
]

_model_reg = None
_model_clf = None
_scaler = None
_meta = None
_model_type = "gb"

def load_models():
    global _model_reg, _model_clf, _scaler, _meta, _model_type
    import joblib
    if _model_reg is None:
        _model_reg = joblib.load(MODEL_DIR / "rf_regressor.pkl")
        _model_clf = joblib.load(MODEL_DIR / "rf_classifier.pkl")
        scaler_path = MODEL_DIR / "scaler.pkl"
        if scaler_path.exists():
            _scaler = joblib.load(scaler_path)
            _model_type = "ridge"
        else:
            _model_type = "gb"
        meta_file = MODEL_DIR / "model_metadata.json"
        if meta_file.exists():
            _meta = json.loads(meta_file.read_text())
            _model_type = _meta.get("model_type", _model_type)
    return _model_reg, _model_clf

def predict_ml_score(factors: dict, llm_score: int = 0) -> dict:
    """
    用ML模型预测股票评分

    Args:
        factors: {"ma5_div": x, "ma10_div": x, "ret5": x, ...}
        llm_score: LLM给出的综合评分（作为特征之一）

    Returns:
        {"ml_score": int, "pred_return": float, "win_prob": float,
         "feature_impression": str, "model_status": str}
    """
    model_reg, model_clf = load_models()

    row = []
    for k in FEATURE_KEYS:
        val = factors.get(k, 0) or 0
        row.append(float(val))
    row[0] = float(llm_score)  # LLM 评分覆盖 score 位置
    X = np.array([row])

    # Ridge 需要标准化
    if _model_type == "ridge" and _scaler is not None:
        X_scaled = _scaler.transform(X)
    else:
        X_scaled = X

    pred_return = float(model_reg.predict(X_scaled)[0])
    win_prob = float(model_clf.predict_proba(X)[0][1])

    raw_score = 50 + pred_return * 3
    ml_score = max(10, min(95, int(raw_score)))

    # 特征贡献
    if hasattr(model_reg, "feature_importances_"):
        imp = model_reg.feature_importances_
    else:
        coef = np.abs(model_reg.coef_).flatten()
        imp = coef / coef.sum()

    top_idx = np.argsort(imp)[-3:][::-1]
    top_feats = [f"{FEATURE_KEYS[i]}({imp[i]:.2f})" for i in top_idx]
    impression = "+".join(top_feats)

    # 模型状态判断
    status = "ok"
    if _meta and _meta.get("full_auc", 1.0) < 0.58:
        status = "degraded"

    return {
        "ml_score": ml_score,
        "pred_return": round(pred_return, 2),
        "win_prob": round(win_prob, 3),
        "feature_impression": impression,
        "model_status": status,
    }

def batch_score(stocks: list) -> list:
    """批量评分股票"""
    results = []
    for s in stocks:
        factors = s.get("factors", {})
        llm_score = s.get("llm_score", 0)
        pred = predict_ml_score(factors, llm_score)
        results.append({
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            **pred,
        })
    return results

def show_model_summary():
    """打印模型摘要"""
    meta_file = MODEL_DIR / "model_metadata.json"
    if not meta_file.exists():
        print("⚠️ 模型元数据不存在，请先训练")
        return
    meta = json.loads(meta_file.read_text())
    print(f"🤖 ML评分模型 摘要")
    print(f"   模型类型: {meta.get('model_type', 'unknown')}")
    print(f"   训练数据: {meta.get('n_features', '?')} 特征")
    print(f"   生成时间: {meta.get('generated_at', '?')}")

    cv = meta.get("cv_results", {})
    for name, vals in cv.items():
        if "r2_mean" in vals:
            print(f"   CV R² ({name}): {vals['r2_mean']:.3f}")
        if "auc_mean" in vals:
            print(f"   CV AUC ({name}): {vals['auc_mean']:.3f}")

    print(f"\n   基线R²: {meta.get('baseline_r2', '?')}")
    print(f"   全量R²: {meta.get('full_r2', '?')}")
    print(f"   全量AUC: {meta.get('full_auc', '?')}")

    print(f"\n   特征重要性Top5 (分类):")
    imp = sorted(meta.get('feature_importance_clf', {}).items(), key=lambda x: -x[1])[:5]
    for name, val in imp:
        print(f"     {name:<12} {val:.3f}")

    print(f"\n   LLM评分权重(分类): {meta.get('feature_importance_clf', {}).get('score', 0):.3f}")
    if meta.get("full_auc", 1.0) < 0.58:
        print("\n   ⚠️ AUC < 0.58 → 建议降级为纯LLM评分")

if __name__ == "__main__":
    show_model_summary()
