#!/usr/bin/env python3
"""
ML评分模型 — 推理接口 v6
2026-09-09：r10目标, GB(n=100,d=3), 19特征, 龙虎榜, 候选池加权
"""
import json
import numpy as np
from pathlib import Path

BASE = Path(__file__).parent.parent
MODEL_DIR = BASE / "data" / "ml_model"

# v7 特征列表 (与 train_ml_model.py 保持一致, 含 dt_norm)
FEATURE_KEYS = [
    "ma5_div", "ma10_div", "ret5", "ret20", "vol20", "vol_ratio",
    "day_range", "ma20_pos", "bias_5", "bias_20", "amplitude",
    "gap_up", "ma20_slope", "ret5_annual",
    "pe", "pb", "score", "is_cand", "has_dragon_tiger", "dt_norm",
]

_model_reg = None
_model_clf = None
_meta = None

def load_models():
    global _model_reg, _model_clf, _meta
    import joblib
    if _model_reg is None:
        # v7: 回归=XGBoost, 分类=GradientBoosting (取 metadata 为准)
        meta_file = MODEL_DIR / "model_metadata.json"
        meta = {}
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
        reg_type = meta.get("model_type", "XGBoost")
        cls_type = meta.get("cls_model", "GradientBoosting")
        reg_path = MODEL_DIR / f"model_{reg_type}.joblib"
        clf_path = MODEL_DIR / f"model_cls_{cls_type}.joblib"
        if not reg_path.exists():
            # 兼容旧版本
            reg_path = MODEL_DIR / "model_GradientBoosting.joblib"
            clf_path = MODEL_DIR / "model_cls_GradientBoosting.joblib"
        if not reg_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {MODEL_DIR}")

        _model_reg = joblib.load(reg_path)
        _model_clf = joblib.load(clf_path)
        _meta = meta
    return _model_reg, _model_clf

def predict_ml_score(factors: dict, llm_score: int = 0) -> dict:
    """
    用ML模型预测股票评分

    Args:
        factors: {"ma5_div": x, "ma10_div": x, ...}
        llm_score: LLM给出的综合评分

    Returns:
        {"ml_score": int, "pred_return": float, "win_prob": float,
         "feature_impression": str, "model_status": str}
    """
    model_reg, model_clf = load_models()

    # 构建特征向量
    row = []
    for k in FEATURE_KEYS:
        val = factors.get(k, 0) or 0
        row.append(float(val))
    # score 位置 = llm_score
    score_idx = FEATURE_KEYS.index("score")
    row[score_idx] = float(llm_score)
    X = np.array([row])

    # 预测
    pred_return = float(model_reg.predict(X)[0])
    win_prob = float(model_clf.predict_proba(X)[0][1])

    # 转换为评分 (r10 目标, 乘以 2 缩放)
    raw_score = 50 + pred_return * 2
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
    print(f"🤖 ML评分模型 {meta.get('version', '?')} 摘要")
    print(f"   模型类型: {meta.get('model_type', 'unknown')}")
    print(f"   目标变量: {meta.get('target', '?')}")
    print(f"   训练数据: {meta.get('n_records', '?')} 条, {meta.get('feature_count', '?')} 特征")
    print(f"   候选池: {meta.get('n_candidates', '?')} 条 (权重 {meta.get('sample_weight', '?')}x)")
    print(f"   生成时间: {meta.get('trained_at', '?')}")
    
    print(f"\n   CV AUC: {meta.get('cv_auc', '?')}")
    print(f"   全量 R²: {meta.get('full_r2', '?')}")
    print(f"   全量 AUC: {meta.get('full_auc', '?')}")
    print(f"   分类 CV AUC: {meta.get('cls_cv_auc', '?')}")
    
    if meta.get("auto_degrade"):
        print("\n   ⚠️ 自动降级: 是 (AUC < 0.58)")
    else:
        print("\n   ✅ 自动降级: 否 (AUC >= 0.58)")

if __name__ == "__main__":
    show_model_summary()