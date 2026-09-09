#!/usr/bin/env python3
"""突破天花板 — 多模型/多目标/多策略实验"""
import json, numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.ensemble import GradientBoostingClassifier
from lightgbm import LGBMClassifier

with open('data/ml_model/dataset_v5.json') as f:
    recs = json.load(f)

base = ['ma5_div','ma10_div','ret5','ret20','vol20','vol_ratio','day_range','ma20_pos','bias_5','bias_20','amplitude','gap_up','ma20_slope','ret5_annual']
tscv = TimeSeriesSplit(n_splits=5)

def run(cls_model, feats, label, target='r3', weight=5.0):
    y = np.array([float(r[target]) for r in recs if r.get(target) is not None])
    yc = (y > 0).astype(int)
    idx = [i for i, r in enumerate(recs) if r.get(target) is not None]
    sw = np.where(np.array([recs[i]['is_cand'] for i in idx]), weight, 1.0)
    X = np.array([[float(recs[i].get(f, 0) or 0) for f in feats] for i in idx], dtype=float)
    
    ca = []
    for tr, te in tscv.split(X):
        c = cls_model.__class__(**cls_model.get_params())
        c.fit(X[tr], yc[tr], sample_weight=sw[tr])
        pp = c.predict_proba(X[te])[:, 1]
        if len(np.unique(yc[te])) > 1:
            ca.append(roc_auc_score(yc[te], pp))
    auc = np.mean(ca)
    print(f'  {label:55s} ({len(feats):2d}f): CV AUC={auc:.4f}')
    return auc

feats_v5 = base + ['pe','pb','score','is_cand','has_dragon_tiger','dt_net_inflow']
feats_simple = base + ['score','is_cand']

print('=== 1. 不同目标变量 ===')
for tgt in ['r3','r5','r10']:
    run(GradientBoostingClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42), feats_v5, f'GB {tgt} (全特征)', tgt)

print('\n=== 2. 不同权重 ===')
for w in [3.0, 5.0, 8.0, 10.0, 15.0]:
    run(GradientBoostingClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42), feats_v5, f'GB r3 weight={w}', 'r3', w)

print('\n=== 3. 不同模型复杂度 ===')
for ne in [30, 50, 100, 200]:
    for md in [2, 3, 5]:
        run(GradientBoostingClassifier(n_estimators=ne, max_depth=md, learning_rate=0.05, random_state=42), feats_v5, f'GB n={ne} d={md}', 'r3')

print('\n=== 4. LGBM 不同参数 ===')
for ne in [30, 50, 100, 200]:
    for nl in [15, 31, 63]:
        run(LGBMClassifier(n_estimators=ne, max_depth=5, num_leaves=nl, learning_rate=0.05, random_state=42, verbose=-1), feats_v5, f'LGBM n={ne} leaves={nl}', 'r3')

print('\n=== 5. 仅候选池训练 ===')
cand_idx = [i for i, r in enumerate(recs) if r['is_cand']]
cand_recs = [recs[i] for i in cand_idx]
X_c = np.array([[float(r.get(f, 0) or 0) for f in feats_v5] for r in cand_recs], dtype=float)
y_c = np.array([float(r['r3']) for r in cand_recs])
yc_c = (y_c > 0).astype(int)
ca = []
for tr, te in tscv.split(X_c):
    c = GradientBoostingClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42)
    c.fit(X_c[tr], yc_c[tr])
    pp = c.predict_proba(X_c[te])[:, 1]
    if len(np.unique(yc_c[te])) > 1:
        ca.append(roc_auc_score(yc_c[te], pp))
print(f'  仅候选池 ({len(feats_v5):2d}f): GB CV AUC={np.mean(ca):.4f} (n={len(X_c)})')

print('\n=== 6. 候选池子模型 + 背景模型 ===')
# 训练候选池子模型
X_cand = np.array([[float(recs[i].get(f, 0) or 0) for f in feats_v5] for i in cand_idx], dtype=float)
y_cand = np.array([float(recs[cand_idx[i]]['r3']) for i in range(len(cand_idx))])
yc_cand = (y_cand > 0).astype(int)

# 训练背景模型
bg_idx = [i for i, r in enumerate(recs) if not r['is_cand']]
X_bg = np.array([[float(recs[i].get(f, 0) or 0) for f in feats_v5] for i in bg_idx], dtype=float)
y_bg = np.array([float(recs[i]['r3']) for i in bg_idx])
yc_bg = (y_bg > 0).astype(int)

# 全量矩阵
X_all = np.array([[float(r.get(f, 0) or 0) for f in feats_v5] for r in recs], dtype=float)
y_all = np.array([float(r['r3']) for r in recs])
yc_all = (y_all > 0).astype(int)

# 分别训练，合并预测
ca = []
for tr, te in tscv.split(X_all):
    cand_mask = np.array([recs[i]['is_cand'] for i in te])
    bg_mask = ~cand_mask
    pp = np.zeros(len(te))
    if cand_mask.sum() > 0 and bg_mask.sum() > 0:
        # 候选池子模型
        te_cand = te[cand_mask]
        tr_cand = tr[cand_mask.sum() if False else 0]  # 简化: 用全量训练
        c1 = GradientBoostingClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42)
        c1.fit(X_all[tr], yc_all[tr])
        pp = c1.predict_proba(X_all[te])[:, 1]
    if len(np.unique(yc_all[te])) > 1:
        ca.append(roc_auc_score(yc_all[te], pp))
print(f'  全量训练 (简化子模型): CV AUC={np.mean(ca):.4f}')

print('\n=== 7. 日期特征 ===')
for r in recs:
    d = r.get('date', '')
    if len(d) >= 10:
        r['dow'] = int(d[8:10])  # 日期中的日号
        r['doy'] = int(d[5:7]) * 10 + int(d[8:10])  # 粗略日期序号
feats_date = base + ['score','is_cand','dow','doy']
run(GradientBoostingClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42), feats_date, '加日期特征', 'r3')