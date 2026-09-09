#!/usr/bin/env python3
"""候选池子模型 + 全量背景模型 对比 — 不依赖XGBoost"""
import json, numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.ensemble import GradientBoostingClassifier

with open('data/ml_model/dataset_v6.json') as f:
    recs = json.load(f)

base = ['ma5_div','ma10_div','ret5','ret20','vol20','vol_ratio','day_range','ma20_pos','bias_5','bias_20','amplitude','gap_up','ma20_slope','ret5_annual']
extra = ['pe','pb','score','is_cand','has_dragon_tiger']
feats = base + extra
tscv = TimeSeriesSplit(n_splits=5)

def make_matrix(idx):
    return np.array([[float(recs[i].get(f,0) or 0) for f in feats] for i in idx], dtype=float)

idx_all = [i for i,r in enumerate(recs) if r.get('r10') is not None]
X = make_matrix(idx_all)
yc = (np.array([float(recs[i]['r10']) for i in idx_all]) > 0).astype(int)
is_cand = np.array([recs[i]['is_cand'] for i in idx_all])

# 1. 全量 + 加权 (当前v6)
sw5 = np.where(is_cand, 5.0, 1.0)
ca=[]
for tr,te in tscv.split(X):
    c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
    c.fit(X[tr],yc[tr],sample_weight=sw5[tr])
    pp=c.predict_proba(X[te])[:,1]
    if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
print(f'1. 全量+5x加权 (v6): CV AUC={np.mean(ca):.4f}')

# 2. 仅候选池训练，预测全量
cand_idx=[i for i in range(len(idx_all)) if is_cand[i]]
Xc=X[cand_idx]; ycc=yc[cand_idx]
ca=[]
for tr,te in tscv.split(X):
    # 用全量训练时的候选池部分
    tr_cand=[j for j in tr if is_cand[j]]
    c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
    c.fit(X[tr_cand],yc[tr_cand])
    pp=c.predict_proba(X[te])[:,1]
    if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
print(f'2. 仅候选池训练→预测全量: CV AUC={np.mean(ca):.4f}')

# 3. 候选池专用评估 (只看候选池测试样本的AUC)
ca_cand=[]
for tr,te in tscv.split(X):
    tr_cand=[j for j in tr if is_cand[j]]
    te_cand=[j for j in te if is_cand[j]]
    if len(te_cand)==0 or len(np.unique(yc[te_cand]))<2: continue
    c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
    c.fit(X[tr_cand],yc[tr_cand])
    pp=c.predict_proba(X[te_cand])[:,1]
    ca_cand.append(roc_auc_score(yc[te_cand],pp))
print(f'3. 候选池训练→候选池测试: CV AUC={np.mean(ca_cand):.4f} (folds={len(ca_cand)}, n_cand={len(cand_idx)})')

# 4. 全量训练→仅评估候选池测试
ca=[]
for tr,te in tscv.split(X):
    c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
    c.fit(X[tr],yc[tr],sample_weight=sw5[tr])
    te_cand=[j for j in te if is_cand[j]]
    if len(te_cand)==0 or len(np.unique(yc[te_cand]))<2: continue
    pp=c.predict_proba(X[te_cand])[:,1]
    ca.append(roc_auc_score(yc[te_cand],pp))
print(f'4. 全量+5x训练→候选池测试: CV AUC={np.mean(ca):.4f} (folds={len(ca)})')

# 5. 不同权重
for w in [3,5,8,10,15]:
    sw=np.where(is_cand,float(w),1.0)
    ca=[]
    for tr,te in tscv.split(X):
        c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
        c.fit(X[tr],yc[tr],sample_weight=sw[tr])
        pp=c.predict_proba(X[te])[:,1]
        if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
    print(f'5. 全量 weight={w}: CV AUC={np.mean(ca):.4f}')

# 6. 去掉pe/pb (基本面数据质量)
feats_nf = base + ['score','is_cand','has_dragon_tiger']
X_nf = np.array([[float(recs[i].get(f,0) or 0) for f in feats_nf] for i in idx_all],dtype=float)
sw5b=np.where(is_cand,5.0,1.0)
ca=[]
for tr,te in tscv.split(X_nf):
    c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
    c.fit(X_nf[tr],yc[tr],sample_weight=sw5b[tr])
    pp=c.predict_proba(X_nf[te])[:,1]
    if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
print(f'6. 去掉pe/pb ({len(feats_nf)}f): CV AUC={np.mean(ca):.4f}')

# 7. 加 dt_net_inflow 归一化
for r in recs:
    r['dt_norm']=r.get('dt_net_inflow',0)/1e8
feats_dt = feats + ['dt_norm']
X_dt = np.array([[float(recs[i].get(f,0) or 0) for f in feats_dt] for i in idx_all],dtype=float)
ca=[]
for tr,te in tscv.split(X_dt):
    c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
    c.fit(X_dt[tr],yc[tr],sample_weight=sw5[tr])
    pp=c.predict_proba(X_dt[te])[:,1]
    if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
print(f'7. 加dt_norm ({len(feats_dt)}f): CV AUC={np.mean(ca):.4f}')

# 8. 时间衰减加权 (越近权重越高)
dates=[recs[i]['date'] for i in idx_all]
max_d=max(dates)
from datetime import datetime
decay=[]
for d in dates:
    age=(datetime.strptime(max_d,'%Y-%m-%d')-datetime.strptime(d,'%Y-%m-%d')).days
    decay.append(np.exp(-age/180))  # 180天半衰
decay=np.array(decay)
sw_decay=decay * np.where(is_cand,5.0,1.0)
ca=[]
for tr,te in tscv.split(X):
    c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
    c.fit(X[tr],yc[tr],sample_weight=sw_decay[tr])
    pp=c.predict_proba(X[te])[:,1]
    if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
print(f'8. 时间衰减(180d)+5x加权: CV AUC={np.mean(ca):.4f}')

for half in [90,180,365]:
    decay=[np.exp(-(datetime.strptime(max_d,'%Y-%m-%d')-datetime.strptime(d,'%Y-%m-%d')).days/half) for d in dates]
    sw_d=np.array(decay)*np.where(is_cand,5.0,1.0)
    ca=[]
    for tr,te in tscv.split(X):
        c=GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42)
        c.fit(X[tr],yc[tr],sample_weight=sw_d[tr])
        pp=c.predict_proba(X[te])[:,1]
        if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
    print(f'9. 时间衰减半衰={half}d: CV AUC={np.mean(ca):.4f}')