---
name: aeon-time-series
description: "Aeon 时间序列机器学习 — 分类、回归、聚类、预测、异常检测"
version: 1.0.0
author: K-Dense-AI (adapted for Hermes Agent)
tags: [aeon, time-series, machine-learning, classification, clustering]
---

# Aeon 时间序列机器学习技能

**来源:** K-Dense-AI/scientific-agent-skills (BSD-3-Clause)
**适配:** Hermes Agent Skill 格式
**适用场景:** 时间序列分类、回归、聚类、预测、异常检测

---

## 何时使用

- 时间序列分类/预测
- 时间序列中的异常/变点检测
- 相似时间序列模式聚类
- 未来值预测
- 重复/异常子序列发现(motif/discord)
- 专用距离度量比较
- 时序特征提取

> **与statsmodels的区别:** statsmodels专注统计推断和经典时间序列模型(ARIMA等); Aeon专注机器学习方法, 适合分类、聚类、复杂模式识别。

---

## 快速上手

### 时间序列分类

```python
from aeon.classification.convolution_based import RocketClassifier
from aeon.datasets import load_classification

X_train, y_train = load_classification("GunPoint", split="train")
X_test, y_test = load_classification("GunPoint", split="test")

clf = RocketClassifier(n_kernels=10000)
clf.fit(X_train, y_train)
accuracy = clf.score(X_test, y_test)
print(f"Accuracy: {accuracy:.4f}")
```

**算法选择:**
- **速度+性能:** MiniRocketClassifier, Arsenal
- **最高精度:** HIVECOTEV2, InceptionTimeClassifier
- **可解释性:** ShapeletTransformClassifier, Catch22Classifier
- **小数据集:** KNeighborsTimeSeriesClassifier (DTW距离)

### 时间序列回归

```python
from aeon.regression.convolution_based import RocketRegressor

X_train, y_train = load_regression("Covid3Month", split="train")
reg = RocketRegressor()
reg.fit(X_train, y_train)
predictions = reg.predict(X_test)
```

### 时间序列聚类

```python
from aeon.clustering import TimeSeriesKMeans

clusterer = TimeSeriesKMeans(n_clusters=3, distance="dtw", averaging_method="ba")
labels = clusterer.fit_predict(X_train)
centers = clusterer.cluster_centers_
```

### 预测

```python
from aeon.forecasting.arima import ARIMA

forecaster = ARIMA(order=(1, 1, 1))
forecaster.fit(y_train)
y_pred = forecaster.predict(fh=[1, 2, 3, 4, 5])
```

### 异常检测

```python
from aeon.anomaly_detection import STOMP

detector = STOMP(window_size=50)
anomaly_scores = detector.fit_predict(y)
threshold = np.percentile(anomaly_scores, 95)
anomalies = anomaly_scores > threshold
```

### 变点分割

```python
from aeon.segmentation import ClaSPSegmenter

segmenter = ClaSPSegmenter()
change_points = segmenter.fit_predict(y)
```

### 相似性搜索

```python
from aeon.similarity_search import StompMotif

motif_finder = StompMotif(window_size=50, k=3)
motifs = motif_finder.fit_predict(y)
```

---

## 特征提取与变换

### ROCKET特征

```python
from aeon.transformations.collection.convolution_based import RocketTransformer

rocket = RocketTransformer()
X_features = rocket.fit_transform(X_train)
```

### 统计特征

```python
from aeon.transformations.collection.feature_based import Catch22

catch22 = Catch22()
X_features = catch22.fit_transform(X_train)
```

### 预处理

```python
from aeon.transformations.collection import Normalizer

scaler = Normalizer()  # Z-normalization
X_normalized = scaler.fit_transform(X_train)
```

---

## 距离度量

```python
from aeon.distances import dtw_distance, dtw_pairwise_distance

distance = dtw_distance(x, y, window=0.1)
distance_matrix = dtw_pairwise_distance(X_train)
```

**可用距离:**
- **弹性距离:** DTW, DDTW, WDTW, ERP, EDR, LCSS, TWE, MSM
- **锁步距离:** Euclidean, Manhattan, Minkowski
- **形状距离:** Shape DTW, SBD

---

## 深度学习网络

**架构:**
- 卷积: FCNClassifier, ResNetClassifier, InceptionTimeClassifier
- 循环: RecurrentNetwork, TCNNetwork
- 自编码器: AEFCNClusterer, AEResNetClusterer

```python
from aeon.classification.deep_learning import InceptionTimeClassifier

clf = InceptionTimeClassifier(n_epochs=100, batch_size=32)
clf.fit(X_train, y_train)
```

---

## 量化金融常用工作流

### 股票走势分类

```python
import pandas as pd
import numpy as np
from aeon.classification.convolution_based import MiniRocketClassifier
from aeon.transformations.collection import Normalizer

# 准备数据: 将收益率序列转为固定长度窗口
def create_windows(returns, window_size=60):
    windows = []
    labels = []
    for i in range(len(returns) - window_size):
        windows.append(returns[i:i+window_size])
        # 标签: 未来20日收益率>0为1, 否则为0
        future_return = returns[i+window_size:i+window_size+20].mean()
        labels.append(1 if future_return > 0 else 0)
    return np.array(windows), np.array(labels)

X, y = create_windows(df['returns'].values)

# 标准化
normalizer = Normalizer()
X_norm = normalizer.fit_transform(X)

# 训练
clf = MiniRocketClassifier(n_kernels=10000)
clf.fit(X_norm, y)

# 预测
y_pred = clf.predict(X_norm)
accuracy = (y_pred == y).mean()
print(f"Accuracy: {accuracy:.4f}")
```

### 成交量异常检测

```python
from aeon.anomaly_detection import STOMP
import numpy as np

# 成交量序列
volume_series = df['volume'].values

# 使用STOMP检测异常
detector = STOMP(window_size=50)
anomaly_scores = detector.fit_predict(volume_series)

# 95%分位数为阈值
threshold = np.percentile(anomaly_scores, 95)
anomalies = anomaly_scores > threshold

# 标记异常日
df['volume_anomaly'] = anomalies
print(f"检测到 {anomalies.sum()} 个成交量异常日")
```

### 股票模式聚类

```python
from aeon.clustering import TimeSeriesKMeans
from aeon.transformations.collection import Normalizer

# 准备价格序列(归一化)
price_windows = []
window_size = 30
for i in range(len(df) - window_size):
    window = df['close'].iloc[i:i+window_size].values
    price_windows.append(window)

X = np.array(price_windows)

# 标准化
normalizer = Normalizer()
X_norm = normalizer.fit_transform(X)

# K-Means聚类(DTW距离)
clusterer = TimeSeriesKMeans(n_clusters=5, distance="dtw", averaging_method="ba")
labels = clusterer.fit_predict(X_norm)

# 分析每个簇的特征
df['pattern'] = np.nan
for i, label in enumerate(labels):
    df.iloc[i:i+window_size, df.columns.get_loc('pattern')] = label

# 查看每个簇的平均形态
for cluster_id in range(5):
    cluster_windows = X_norm[labels == cluster_id]
    print(f"Cluster {cluster_id}: {len(cluster_windows)} 个窗口")
```

---

## 依赖安装

```bash
# 方案一（推荐，使用 uv）：
uv pip install aeon numpy pandas matplotlib
# 方案二（无 uv 时回退）：
# pip install aeon numpy pandas matplotlib
```

---

## 注意事项

⚠️ **重要提示:**
1. Aeon与scikit-learn API兼容, 可无缝集成到现有Pipeline
2. 时间序列分类需固定长度窗口, 可用padding处理不等长
3. DTW距离计算量大, 大数据集用MiniRocket
4. 金融时间序列常非平稳, 先做差分或归一化
5. 异常检测阈值需根据业务调整(95%分位是起点)
6. 聚类结果需结合业务含义解读
