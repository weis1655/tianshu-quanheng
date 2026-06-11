#!/usr/bin/env python3
"""
ML评分模型 — 推理接口
在审查阶段调用: 用6因子+训练好的随机森林替代LLM评分
"""
import json, sys
import numpy as np
from pathlib import Path

BASE = Path(__file__).parent.parent
MODEL_DIR = BASE / "data" / "ml_model"

FEATURE_KEYS = ["score", "ma5_div", "ma10_div", "ret5", "ret20", "vol20", "vol_ratio", "day_range", "ma20_pos"]

# 缓存模型
_model_reg = None
_model_clf = None

def load_models():
    global _model_reg, _model_clf
    import joblib
    if _model_reg is None:
        _model_reg = joblib.load(MODEL_DIR / "rf_regressor.pkl")
        _model_clf = joblib.load(MODEL_DIR / "rf_classifier.pkl")
    return _model_reg, _model_clf

def predict_ml_score(factors: dict, llm_score: int = 0) -> dict:
    """
    用ML模型预测股票评分
    
    Args:
        factors: {"ma5_div": x, "ma10_div": x, "ret5": x, "ret20": x, 
                  "vol20": x, "vol_ratio": x, "day_range": x, "ma20_pos": x}
        llm_score: LLM给出的综合评分（作为特征之一）
    
    Returns:
        {"ml_score": int, "pred_return": float, "win_prob": float, 
         "feature_impression": str}
    """
    model_reg, model_clf = load_models()
    
    # 构建特征向量
    row = []
    for k in FEATURE_KEYS:
        val = factors.get(k, 0) or 0
        row.append(float(val))
    # 用llm_score覆盖第一个特征
    row[0] = float(llm_score)
    
    X = np.array([row])
    
    # 预测
    pred_return = float(model_reg.predict(X)[0])
    win_prob = float(model_clf.predict_proba(X)[0][1])  # P(r3 > 0)
    
    # 映射到0-100分
    # pred_return 范围大约-15%~15%, 映射到0-100
    raw_score = 50 + pred_return * 3  # 1%回报 ≈ 3分
    ml_score = max(10, min(95, int(raw_score)))
    
    # 特征贡献描述
    imp = model_reg.feature_importances_
    top_idx = np.argsort(imp)[-3:][::-1]
    top_feats = [f"{FEATURE_KEYS[i]}({imp[i]:.2f})" for i in top_idx]
    impression = "+".join(top_feats)
    
    return {
        "ml_score": ml_score,
        "pred_return": round(pred_return, 2),
        "win_prob": round(win_prob, 3),
        "feature_impression": impression,
    }

def batch_score(stocks: list) -> list:
    """
    批量评分股票
    
    Args:
        stocks: [{"code": x, "name": x, "factors": {...}, "llm_score": x}, ...]
    
    Returns:
        [{"code": x, "name": x, "ml_score": x, ...}, ...]
    """
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
    import json
    meta_file = MODEL_DIR / "model_metadata.json"
    if not meta_file.exists():
        print("⚠️ 模型元数据不存在，请先训练")
        return
    meta = json.loads(meta_file.read_text())
    print(f"🤖 ML评分模型 摘要")
    print(f"   训练数据: {meta['n_records']} 条")
    print(f"   特征: {len(meta['feature_names'])} 个")
    print(f"   交叉验证准确率(分类): {meta['cv_acc_mean']*100:.1f}% (基准50%)")
    print(f"\n   特征重要性Top5:")
    imp = sorted(meta['feature_importance_clf'].items(), key=lambda x: -x[1])[:5]
    for name, val in imp:
        print(f"     {name:<12} {val:.3f}")
    print(f"\n   LLM评分特征重要性(分类): {meta['feature_importance_clf'].get('score', 0):.3f}")
    print(f"   说明: score越低, LLM评分对预测的贡献越小")

if __name__ == "__main__":
    show_model_summary()