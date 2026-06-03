---
name: scikit-learn
description: "Scikit-learn 机器学习建模 — 分类回归、聚类降维、特征工程、模型评估"
version: 1.0.0
author: K-Dense-AI (adapted for Hermes Agent)
tags: [scikit-learn, machine-learning, classification, regression, pipeline]
---

# Scikit-learn Skill

**来源:** K-Dense-AI/scientific-agent-skills (BSD-3-Clause)
**适配:** Hermes Agent Skill 格式
**适用场景:** 机器学习建模、分类回归、聚类、特征工程

---

## 何时使用

- 构建分类或回归模型
- 执行聚类或降维
- 数据预处理和特征变换
- 模型评估与交叉验证
- 超参数调优(网格搜索/随机搜索)
- 创建ML Pipeline用于生产流程
- 比较不同算法
- 结构化(表格)和文本数据
- 需要可解释的经典机器学习方法

---

## 快速上手

### 分类示例

```python
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report

# 分割数据
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, stratify=y, random_state=42
)

# 预处理
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

# 训练模型
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train_scaled, y_train)

# 评估
y_pred = model.predict(X_test_scaled)
print(classification_report(y_test, y_pred))
```

### 完整Pipeline(混合数据类型)

```python
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.ensemble import GradientBoostingClassifier

# 定义特征类型
numeric_features = ['age', 'income']
categorical_features = ['gender', 'occupation']

# 创建预处理Pipeline
numeric_transformer = Pipeline([
    ('imputer', SimpleImputer(strategy='median')),
    ('scaler', StandardScaler())
])
categorical_transformer = Pipeline([
    ('imputer', SimpleImputer(strategy='most_frequent')),
    ('onehot', OneHotEncoder(handle_unknown='ignore'))
])

# 合并transformers
preprocessor = ColumnTransformer([
    ('num', numeric_transformer, numeric_features),
    ('cat', categorical_transformer, categorical_features)
])

# 完整Pipeline
model = Pipeline([
    ('preprocessor', preprocessor),
    ('classifier', GradientBoostingClassifier(random_state=42))
])

# 拟合和预测
model.fit(X_train, y_train)
y_pred = model.predict(X_test)
```

### 回归示例

```python
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, r2_score

model = Ridge(alpha=1.0)
model.fit(X_train, y_train)

y_pred = model.predict(X_test)
mse = mean_squared_error(y_test, y_pred)
r2 = r2_score(y_test, y_pred)

print(f"MSE: {mse:.4f}")
print(f"R²: {r2:.4f}")
```

---

## 核心能力清单

### 1. 监督学习

**线性模型:**
- Logistic Regression, Linear Regression
- Ridge, Lasso, ElasticNet

**树模型:**
- Decision Trees, Random Forest
- Gradient Boosting, XGBoost(需单独安装)

**支持向量机:**
- SVC, SVR (各种kernel)

**集成方法:**
- AdaBoost, Voting, Stacking

**神经网络:**
- MLPClassifier, MLPRegressor

**其他:**
- Naive Bayes, K-Nearest Neighbors

### 2. 无监督学习

**聚类:**
- K-Means, MiniBatchKMeans
- DBSCAN, HDBSCAN, OPTICS
- AgglomerativeClustering
- Gaussian Mixture Models
- MeanShift, SpectralClustering, BIRCH

**降维:**
- PCA, TruncatedSVD, NMF
- t-SNE, UMAP, Isomap, LLE
- FastICA, LatentDirichletAllocation

### 3. 模型评估与选择

**交叉验证:**
- KFold, StratifiedKFold
- TimeSeriesSplit (时间序列专用)
- GroupKFold

**超参数调优:**
- GridSearchCV, RandomizedSearchCV
- HalvingGridSearchCV

**评估指标:**
- 分类: accuracy, precision, recall, F1-score, ROC AUC, confusion matrix
- 回归: MSE, RMSE, MAE, R², MAPE
- 聚类: silhouette score, Calinski-Harabasz, Davies-Bouldin

### 4. 数据预处理

**缩放:**
- StandardScaler, MinMaxScaler, RobustScaler, Normalizer

**编码:**
- OneHotEncoder, OrdinalEncoder, LabelEncoder

**缺失值处理:**
- SimpleImputer, KNNImputer, IterativeImputer

**特征工程:**
- PolynomialFeatures, KBinsDiscretizer
- 特征选择: RFE, SelectKBest, SelectFromModel

### 5. Pipeline与组合

**组件:**
- Pipeline, ColumnTransformer, FeatureUnion
- TransformedTargetRegressor

**优势:**
- 防止数据泄露
- 简化代码
- 支持联合超参数调优
- 确保一致性

---

## 量化金融常用工作流

### 股票分类模型

```python
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report, roc_auc_score
import numpy as np

# 创建标签: 未来N日收益率>阈值则为1
def create_labels(returns, horizon=5, threshold=0.02):
    future_returns = returns.shift(-horizon).fillna(0)
    return (future_returns > threshold).astype(int)

y = create_labels(df['returns'])
X = df[['rsi', 'macd', 'volatility', 'volume_ratio']]

# 时间序列交叉验证
tscv = TimeSeriesSplit(n_splits=5)

for train_idx, test_idx in tscv.split(X):
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    
    model = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42)
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    
    print(classification_report(y_test, y_pred))
    print(f"ROC AUC: {roc_auc_score(y_test, y_proba):.4f}")
    
    # 特征重要性
    importances = pd.Series(model.feature_importances_, X.columns)
    print(importances.sort_values(ascending=False))
```

### 投资组合优化 (均值-方差)

```python
import numpy as np
from scipy.optimize import minimize

def portfolio_variance(weights, cov_matrix):
    return np.dot(weights.T, np.dot(cov_matrix, weights))

def optimize_portfolio(returns, target_return=None):
    n_assets = len(returns.columns)
    cov_matrix = returns.cov()
    mean_returns = returns.mean()
    
    # 最小化方差
    constraints = {'type': 'eq', 'fun': lambda w: np.sum(w) - 1}
    bounds = tuple((0, 1) for _ in range(n_assets))
    initial_weights = np.array([1/n_assets] * n_assets)
    
    result = minimize(
        portfolio_variance,
        initial_weights,
        args=(cov_matrix,),
        method='SLSQP',
        bounds=bounds,
        constraints=constraints
    )
    
    return result.x

# 有效前沿
efficient_frontier = []
for target in np.linspace(returns.mean().min(), returns.mean().max(), 20):
    # ... 带目标收益约束的优化
    pass
```

### 异常检测 (孤立森林)

```python
from sklearn.ensemble import IsolationForest

# 检测交易异常
features = ['volume', 'volatility', 'price_change']
X = df[features]

iso_forest = IsolationForest(contamination=0.05, random_state=42)
anomalies = iso_forest.fit_predict(X)

# -1为异常, 1为正常
df['anomaly'] = anomalies
anomaly_trades = df[anomalies == -1]
print(f"检测到 {len(anomaly_trades)} 笔异常交易")
```

---

## 算法选择指南

| 任务 | 首选算法 | 备选 |
|------|---------|------|
| 表格数据分类 | Random Forest, XGBoost | Gradient Boosting, SVM |
| 表格数据回归 | XGBoost, LightGBM | Random Forest, Ridge |
| 高维稀疏数据 | Logistic Regression | Linear SVM |
| 小数据集 | SVM, KNN | Random Forest |
| 需要可解释性 | Decision Tree, Linear models | — |
| 聚类 | K-Means(球形), DBSCAN(任意形状) | HDBSCAN, GMM |
| 降维可视化 | PCA | t-SNE, UMAP |
| 时间序列 | TimeSeriesSplit + 任意模型 | — |

---

## 注意事项

⚠️ **重要提示:**
1. **数据泄露预防:** scaler.fit_transform(train), scaler.transform(test)
2. **时间序列:** 必须用 TimeSeriesSplit, 不能用随机KFold
3. **类别不平衡:** 用 stratify=y 分割, 或 class_weight='balanced'
4. **特征缩放:** 树模型不需要, 线性/SVM需要
5. **过拟合:** 小数据集用交叉验证, 大模型用正则化
6. **类别编码:** 有序用 OrdinalEncoder, 名义用 OneHotEncoder

---

## 依赖安装

```bash
# 方案一（推荐，使用 uv）：
uv pip install scikit-learn pandas numpy matplotlib seaborn
# 方案二（无 uv 时回退）：
# pip install scikit-learn pandas numpy matplotlib seaborn
# 增强版
# uv pip install xgboost lightgbm imbalanced-learn
# pip install xgboost lightgbm imbalanced-learn
```
