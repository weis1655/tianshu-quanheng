#!/usr/bin/env python3
"""特征消融实验 — 找最佳特征组合"""
import json, numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from lightgbm import LGBMClassifier

with open('data/ml_model/dataset_v5.json') as f:
    recs = json.load(f)

base = ['ma5_div','ma10_div','ret5','ret20','vol20','vol_ratio','day_range','ma20_pos','bias_5','bias_20','amplitude','gap_up','ma20_slope','ret5_annual']
tscv = TimeSeriesSplit(n_splits=5)
sw = np.where(np.array([r['is_cand'] for r in recs]), 5.0, 1.0)
y = np.array([float(r['r3']) for r in recs])
yc = (y > 0).astype(int)

def run(feats, label):
    X = np.array([[float(r.get(f, 0) or 0) for f in feats] for r in recs], dtype=float)
    ca = []
    for tr, te in tscv.split(X):
        c = LGBMClassifier(n_estimators=50, max_depth=5, learning_rate=0.05, num_leaves=31, random_state=42, verbose=-1)
        c.fit(X[tr], yc[tr], sample_weight=sw[tr])
        pp = c.predict_proba(X[te])[:, 1]
        if len(np.unique(yc[te])) > 1:
            ca.append(roc_auc_score(yc[te], pp))
    auc = np.mean(ca)
    print(f'  {label:50s} ({len(feats):2d}f): LGBM CV AUC={auc:.4f}')
    return auc

print('=== 特征消融实验 ===\n')
run(base + ['pe','pb','score','is_cand','has_dragon_tiger','dt_net_inflow'], '当前v5')
run(base + ['pe','pb','score','is_cand','has_dragon_tiger'], '去掉dt_net_inflow')
run(base + ['score','is_cand'], '仅技术+score+is_cand')
run(base + ['is_cand'], '仅技术+is_cand')
run(base + ['pe','pb','score','is_cand'], '技术+基本面+score+is_cand')

# dt_net_inflow 归一化
for r in recs:
    r['dt_norm'] = r.get('dt_net_inflow', 0) / 1e8
run(base + ['pe','pb','score','is_cand','has_dragon_tiger','dt_norm'], 'dt_net_inflow/1e8归一化')

# 交叉特征
for r in recs:
    r['vol_x_price'] = r.get('vol_ratio', 0) * r.get('ma20_pos', 0)
    r['bias_x_score'] = r.get('bias_5', 0) * r.get('score', 50)
run(base + ['pe','pb','score','is_cand','vol_x_price','bias_x_score'], '加交叉特征')

# 去掉pb和pe (基本面数据质量差)
run(base + ['score','is_cand','has_dragon_tiger'], '技术+score+is_cand+龙虎榜')
run(base + ['score','is_cand','has_dragon_tiger','vol_x_price','bias_x_score'], '技术+score+is_cand+龙虎榜+交叉')