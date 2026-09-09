#!/usr/bin/env python3
"""验证日期泄漏 + r10 调参"""
import json, numpy as np
from datetime import datetime
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.ensemble import GradientBoostingClassifier

with open('data/ml_model/dataset_v5.json') as f:
    recs = json.load(f)

base = ['ma5_div','ma10_div','ret5','ret20','vol20','vol_ratio','day_range','ma20_pos','bias_5','bias_20','amplitude','gap_up','ma20_slope','ret5_annual']
extra = ['pe','pb','score','is_cand','has_dragon_tiger','dt_net_inflow']
tscv = TimeSeriesSplit(n_splits=5)

# 日期特征
for r in recs:
    d = r.get('date', '')
    if len(d) >= 10:
        dt = datetime.strptime(d, '%Y-%m-%d')
        r['doy'] = dt.timetuple().tm_yday

def eval_cv(feats, target='r3', ne=100, md=2, weight=5.0, label=''):
    idx = [i for i, r in enumerate(recs) if r.get(target) is not None]
    X = np.array([[float(recs[i].get(f, 0) or 0) for f in feats] for i in idx], dtype=float)
    y = np.array([float(recs[i][target]) for i in idx])
    yc = (y > 0).astype(int)
    sw = np.where(np.array([recs[i]['is_cand'] for i in idx]), weight, 1.0)
    ca = []
    for tr, te in tscv.split(X):
        c = GradientBoostingClassifier(n_estimators=ne, max_depth=md, learning_rate=0.05, random_state=42)
        c.fit(X[tr], yc[tr], sample_weight=sw[tr])
        pp = c.predict_proba(X[te])[:, 1]
        if len(np.unique(yc[te])) > 1:
            ca.append(roc_auc_score(yc[te], pp))
    return np.mean(ca)

feats_base = base + extra
feats_date = base + ['score','is_cand','doy']

print('=== 日期泄漏验证 ===')
auc1 = eval_cv(feats_base, 'r3', 100, 2, 5.0)
print(f'  无日期 r3: {auc1:.4f}')
auc2 = eval_cv(feats_date, 'r3', 100, 2, 5.0)
print(f'  有日期 r3: {auc2:.4f}')
print(f'  日期增益: {auc2-auc1:+.4f}')

# 特征重要性
X_all = np.array([[float(r.get(f, 0) or 0) for f in feats_date] for r in recs], dtype=float)
yc_all = ((np.array([float(r['r3']) for r in recs]) > 0).astype(int))
sw_all = np.where(np.array([r['is_cand'] for r in recs]), 5.0, 1.0)
c = GradientBoostingClassifier(n_estimators=100, max_depth=2, learning_rate=0.05, random_state=42)
c.fit(X_all, yc_all, sample_weight=sw_all)
imp = sorted(zip(feats_date, c.feature_importances_), key=lambda x: -x[1])[:8]
print(f'\n  特征重要性 (含日期):')
for n, v in imp:
    print(f'    {n:20s}: {v:.4f}')

print('\n=== r10 目标变量 ===')
auc_r10 = eval_cv(feats_base, 'r10', 100, 2, 5.0)
print(f'  r10 (n=100,d=2): {auc_r10:.4f}')

for ne, md in [(50,2),(100,2),(100,3),(200,2)]:
    a = eval_cv(feats_base, 'r10', ne, md, 5.0)
    print(f'  r10 (n={ne},d={md}): {a:.4f}')

print('\n=== 最终对比 ===')
for tgt in ['r3','r5','r10']:
    a = eval_cv(feats_base, tgt, 100, 2, 5.0)
    print(f'  {tgt}: {a:.4f}')

# 无基本面特征
feats_no_fund = base + ['score','is_cand']
print('\n=== 去基本面 ===')
for tgt in ['r3','r5','r10']:
    a = eval_cv(feats_no_fund, tgt, 100, 2, 5.0)
    print(f'  {tgt} (无基本面): {a:.4f}')