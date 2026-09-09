#!/usr/bin/env python3
"""XGBoost vs GB vs LightGBM 对比 — 找最强模型"""
import json, numpy as np
from sklearn.metrics import roc_auc_score, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from lightgbm import LGBMRegressor, LGBMClassifier
from xgboost import XGBRegressor, XGBClassifier

with open('data/ml_model/dataset_v6.json') as f:
    recs = json.load(f)

base = ['ma5_div','ma10_div','ret5','ret20','vol20','vol_ratio','day_range','ma20_pos','bias_5','bias_20','amplitude','gap_up','ma20_slope','ret5_annual']
tscv = TimeSeriesSplit(n_splits=5)
WEIGHT = 5.0

def prepare(feats):
    idx = [i for i,r in enumerate(recs) if r.get('r10') is not None]
    for r in recs:
        if 'dt_norm' not in r: r['dt_norm']=r.get('dt_net_inflow',0)/1e8
    X = np.array([[float(recs[i].get(f,0) or 0) for f in feats] for i in idx],dtype=float)
    y = np.array([float(recs[i]['r10']) for i in idx])
    yc = (y>0).astype(int)
    sw = np.where(np.array([recs[i]['is_cand'] for i in idx]), WEIGHT, 1.0)
    return X, y, yc, sw

def eval_reg(model_factory, feats, label, sw):
    X,y,yc,swp = prepare(feats)
    cv_r2=[]; cv_auc=[]
    for tr,te in tscv.split(X):
        m=model_factory()
        m.fit(X[tr],y[tr],sample_weight=swp[tr])
        p=m.predict(X[te])
        cv_r2.append(r2_score(y[te],p))
        if len(np.unique(yc[te]))>1: cv_auc.append(roc_auc_score(yc[te],p))
    print(f'  {label:38s} ({len(feats):2d}f): CV R2={np.mean(cv_r2):.4f} AUC={np.mean(cv_auc):.4f}')
    return np.mean(cv_auc)

def eval_cls(model_factory, feats, label, sw):
    X,y,yc,swp = prepare(feats)
    ca=[]
    for tr,te in tscv.split(X):
        m=model_factory()
        m.fit(X[tr],yc[tr],sample_weight=swp[tr])
        pp=m.predict_proba(X[te])[:,1]
        if len(np.unique(yc[te]))>1: ca.append(roc_auc_score(yc[te],pp))
    print(f'  {label:38s} ({len(feats):2d}f): CV AUC={np.mean(ca):.4f}')
    return np.mean(ca)

f_no_dt = base + ['pe','pb','score','is_cand','has_dragon_tiger']
f_dt = f_no_dt + ['dt_norm']

print('=== 回归模型对比 (无dt_norm) ===')
eval_reg(lambda: GradientBoostingRegressor(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42), f_no_dt, 'GB n=100 d=3', WEIGHT)
eval_reg(lambda: GradientBoostingRegressor(n_estimators=200,max_depth=3,learning_rate=0.05,random_state=42), f_no_dt, 'GB n=200 d=3', WEIGHT)
eval_reg(lambda: LGBMRegressor(n_estimators=100,max_depth=5,num_leaves=31,learning_rate=0.05,random_state=42,verbose=-1), f_no_dt, 'LGBM n=100', WEIGHT)
eval_reg(lambda: XGBRegressor(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42,eval_metric='rmse'), f_no_dt, 'XGB n=100 d=3', WEIGHT)
eval_reg(lambda: XGBRegressor(n_estimators=200,max_depth=3,learning_rate=0.05,random_state=42,eval_metric='rmse'), f_no_dt, 'XGB n=200 d=3', WEIGHT)
eval_reg(lambda: XGBRegressor(n_estimators=100,max_depth=5,num_leaves=31,learning_rate=0.05,random_state=42,eval_metric='rmse'), f_no_dt, 'XGB n=100 d=5', WEIGHT)
eval_reg(lambda: XGBRegressor(n_estimators=200,max_depth=3,learning_rate=0.03,random_state=42,eval_metric='rmse',subsample=0.8,colsample_bytree=0.8), f_no_dt, 'XGB n=200 lr=0.03 正则', WEIGHT)

print('\n=== 回归模型对比 (含dt_norm) ===')
eval_reg(lambda: GradientBoostingRegressor(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42), f_dt, 'GB n=100 d=3', WEIGHT)
eval_reg(lambda: XGBRegressor(n_estimators=200,max_depth=3,learning_rate=0.03,random_state=42,eval_metric='rmse',subsample=0.8,colsample_bytree=0.8), f_dt, 'XGB n=200 lr=0.03 正则', WEIGHT)

print('\n=== 分类模型对比 (无dt_norm) ===')
eval_cls(lambda: GradientBoostingClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42), f_no_dt, 'GB n=100 d=3', WEIGHT)
eval_cls(lambda: LGBMClassifier(n_estimators=100,max_depth=5,num_leaves=31,learning_rate=0.05,random_state=42,verbose=-1), f_no_dt, 'LGBM n=100', WEIGHT)
eval_cls(lambda: XGBClassifier(n_estimators=100,max_depth=3,learning_rate=0.05,random_state=42,eval_metric='logloss'), f_no_dt, 'XGB n=100 d=3', WEIGHT)
eval_cls(lambda: XGBClassifier(n_estimators=200,max_depth=3,learning_rate=0.05,random_state=42,eval_metric='logloss'), f_no_dt, 'XGB n=200 d=3', WEIGHT)
eval_cls(lambda: XGBClassifier(n_estimators=200,max_depth=3,learning_rate=0.03,random_state=42,eval_metric='logloss',subsample=0.8,colsample_bytree=0.8), f_no_dt, 'XGB n=200 lr=0.03 正则', WEIGHT)
eval_cls(lambda: XGBClassifier(n_estimators=100,max_depth=5,num_leaves=31,learning_rate=0.05,random_state=42,eval_metric='logloss'), f_no_dt, 'XGB n=100 d=5', WEIGHT)

print('\n=== 分类模型对比 (含dt_norm) ===')
eval_cls(lambda: XGBClassifier(n_estimators=200,max_depth=3,learning_rate=0.03,random_state=42,eval_metric='logloss',subsample=0.8,colsample_bytree=0.8), f_dt, 'XGB n=200 lr=0.03 正则', WEIGHT)