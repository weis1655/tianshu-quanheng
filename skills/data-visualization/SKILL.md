---
name: data-visualization
description: "Matplotlib 数据可视化 — 科学绘图、报告图表生成、量化金融图表"
version: 1.0.0
author: K-Dense-AI (adapted for Hermes Agent)
tags: [matplotlib, visualization, plotting, charts, finance]
---

# 数据可视化技能

**来源:** K-Dense-AI/scientific-agent-skills (MIT)
**适配:** Hermes Agent Skill 格式
**适用场景:** 科学绘图、数据可视化、报告图表生成

---

## 何时使用

- 生成报告所需的图表和可视化
- 分析股票价格走势、收益率分布
- 创建量化金融分析中的专业图表
- 制作相关性热力图、策略对比图等
- 需要高质量、出版级的数据可视化输出

---

## 核心架构

### Matplotlib 层次结构

1. **Figure**: 顶层容器, 包含所有绘图元素
2. **Axes**: 实际绘图区域(一个Figure可含多个Axes)
3. **Artist**: 所有可见元素(线条、文字、刻度)
4. **Axis**: 数轴线对象(x轴/y轴), 处理刻度/标签

### 两种接口

| 接口 | 描述 | 推荐场景 |
|------|------|---------|
| **pyplot (隐式)** | MATLAB风格, 自动维护状态 | 快速交互式探索 |
| **面向对象 (显式)** | `fig, ax = plt.subplots()` | **推荐**(复杂图表、维护) |

```python
# OO接口(推荐)
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([1, 2, 3, 4])
ax.set_ylabel('some numbers')
plt.show()
```

---

## 基本绘图

```python
import matplotlib.pyplot as plt
import numpy as np

fig, ax = plt.subplots(figsize=(10, 6))
x = np.linspace(0, 2*np.pi, 100)
ax.plot(x, np.sin(x), label='sin(x)')
ax.plot(x, np.cos(x), label='cos(x)')

ax.set_xlabel('x')
ax.set_ylabel('y')
ax.set_title('Trigonometric Functions')
ax.legend()
ax.grid(True, alpha=0.3)

plt.savefig('plot.png', dpi=300, bbox_inches='tight')
plt.show()
```

---

## 子图布局

### 规则网格

```python
fig, axes = plt.subplots(2, 2, figsize=(12, 10))
axes[0, 0].plot(x, y1)
axes[0, 1].scatter(x, y2)
axes[1, 0].hist(data)
axes[1, 1].bar(categories, values)
```

### GridSpec (最大控制)

```python
from matplotlib.gridspec import GridSpec

fig = plt.figure(figsize=(12, 8))
gs = GridSpec(3, 3, figure=fig)
ax1 = fig.add_subplot(gs[0, :])      # 顶行, 全列
ax2 = fig.add_subplot(gs[1:, 0])     # 底两行, 第一列
ax3 = fig.add_subplot(gs[1:, 1:])    # 底两行, 最后两列
```

---

## 图表类型与用途

| 类型 | 用途 | 代码片段 |
|------|------|---------|
| **折线图** | 时间序列、趋势 | `ax.plot(x, y, linewidth=2, linestyle='--')` |
| **散点图** | 关系、相关性 | `ax.scatter(x, y, c=colors, cmap='viridis')` |
| **柱状图** | 分类比较 | `ax.bar(categories, values)` 或 `ax.barh(...)` |
| **直方图** | 分布 | `ax.hist(data, bins=30, edgecolor='black')` |
| **热力图** | 矩阵数据、相关性 | `ax.imshow(matrix, cmap='coolwarm')` |
| **箱线图** | 统计分布 | `ax.boxplot([data1, data2])` |
| ** violin图** | 分布密度 | `ax.violinplot([data1, data2])` |

---

## 样式与定制

### 颜色
- 命名: `'red'`, `'blue'`
- Hex: `'#FF5733'`
- RGB元组: `(0.1, 0.2, 0.5)`
- 色图: `cmap='viridis'`, `cmap='coolwarm'`

### 样式表

```python
plt.style.use('seaborn-v0_8-darkgrid')
plt.style.use('ggplot')
plt.style.use('bmh')
```

### 注释

```python
ax.text(x, y, 'annotation', fontsize=12, ha='center')
ax.annotate('point', xy=(x, y), 
            arrowprops=dict(arrowstyle='->'),
            xytext=(x+0.5, y+0.5))
```

---

## 保存图表

```python
# PNG (演示/网页)
plt.savefig('figure.png', dpi=300, bbox_inches='tight', facecolor='white')

# PDF/SVG (出版/矢量)
plt.savefig('figure.pdf', dpi=300, bbox_inches='tight')
plt.savefig('figure.svg', bbox_inches='tight')

# 透明背景
plt.savefig('figure.png', transparent=True, bbox_inches='tight')
```

---

## 量化金融常用工作流

### 股票价格与成交量

```python
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

fig, ax1 = plt.subplots(figsize=(14, 6))

# 价格(左轴)
color = 'tab:blue'
ax1.set_xlabel('Date')
ax1.set_ylabel('Price', color=color)
ax1.plot(df.index, df['close'], color=color, linewidth=1.5, label='Close')
ax1.tick_params(axis='y', labelcolor=color)
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=1))

# 成交量(右轴)
ax2 = ax1.twinx()
color = 'tab:gray'
ax2.set_ylabel('Volume', color=color)
ax2.bar(df.index, df['volume'], color=color, alpha=0.3, width=1)
ax2.tick_params(axis='y', labelcolor=color)

fig.tight_layout()
plt.savefig('stock_price_volume.png', dpi=300, bbox_inches='tight')
plt.show()
```

### 收益率分布

```python
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

# 直方图
axes[0].hist(returns, bins=50, edgecolor='black', alpha=0.7)
axes[0].set_title('Returns Histogram')
axes[0].axvline(returns.mean(), color='red', linestyle='--', label=f'Mean: {returns.mean():.4f}')
axes[0].legend()

# Q-Q图
from scipy import stats
stats.probplot(returns, dist="norm", plot=axes[1])
axes[1].set_title('Q-Q Plot')

# 箱线图
axes[2].boxplot([returns, log_returns], labels=['Returns', 'Log Returns'])
axes[2].set_title('Box Plot')

plt.tight_layout()
plt.savefig('returns_distribution.png', dpi=300, bbox_inches='tight')
plt.show()
```

### 相关性热力图

```python
import seaborn as sns

# 计算相关性矩阵
corr_matrix = df[['close', 'volume', 'rsi', 'macd', 'volatility']].corr()

fig, ax = plt.subplots(figsize=(8, 6))
sns.heatmap(corr_matrix, annot=True, cmap='coolwarm', center=0,
            square=True, linewidths=1, ax=ax, fmt='.2f')
ax.set_title('Feature Correlation Matrix')
plt.savefig('correlation_heatmap.png', dpi=300, bbox_inches='tight')
plt.show()
```

### 多股票对比

```python
fig, ax = plt.subplots(figsize=(14, 7))

colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
for i, stock in enumerate(stocks):
    # 归一化到起始点
    normalized = stock['close'] / stock['close'].iloc[0]
    ax.plot(stock.index, normalized, label=stock['name'], 
            color=colors[i], linewidth=2)

ax.set_xlabel('Date')
ax.set_ylabel('Normalized Price (Start=1.0)')
ax.set_title('Stock Performance Comparison')
ax.legend(loc='upper left')
ax.grid(True, alpha=0.3)
plt.savefig('stock_comparison.png', dpi=300, bbox_inches='tight')
plt.show()
```

### 夏普比率对比

```python
fig, ax = plt.subplots(figsize=(10, 6))

strategies = ['Buy & Hold', 'RSI Strategy', 'MACD Strategy', 'ML Model']
sharpe_ratios = [0.85, 1.12, 0.95, 1.35]
colors = ['#2ca02c' if s > 1 else '#ff7f0e' for s in sharpe_ratios]

bars = ax.bar(strategies, sharpe_ratios, color=colors, edgecolor='black')

# 添加数值标签
for bar, val in zip(bars, sharpe_ratios):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
            f'{val:.2f}', ha='center', va='bottom', fontsize=11)

ax.axhline(y=1, color='red', linestyle='--', alpha=0.5, label='Sharpe=1')
ax.set_ylabel('Sharpe Ratio')
ax.set_title('Strategy Sharpe Ratio Comparison')
ax.legend()
ax.set_ylim(0, max(sharpe_ratios) * 1.2)
plt.savefig('sharpe_comparison.png', dpi=300, bbox_inches='tight')
plt.show()
```

---

## 最佳实践

### 1. 界面与结构
- **始终用面向对象接口** 用于生产代码
- **组织为函数** 便于复用:
```python
def create_analysis_plot(data, title):
    fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
    ax.plot(data['x'], data['y'], linewidth=2)
    ax.set_xlabel('X Axis Label', fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold')
    return fig, ax
```

### 2. 图尺寸与DPI
- **创建时设置 figsize:** `plt.subplots(figsize=(10, 6))`
- **DPI标准:**
  - 屏幕/笔记本: 72-100 dpi
  - 网页: 150 dpi
  - 打印/出版: **300 dpi**

### 3. 布局管理
- 用 `constrained_layout=True` 或 `tight_layout()` 防止元素重叠
- 推荐: `fig, ax = plt.subplots(constrained_layout=True)`

### 4. 色图选择
- **顺序色图** (`viridis`, `plasma`): 有序数据
- **发散色图** (`coolwarm`, `RdBu`): 有中心值的数据(如零)
- **定性色图** (`tab10`, `Set2`): 分类数据

---

## 依赖安装

```bash
# 方案一（推荐，使用 uv）：
uv pip install matplotlib seaborn pandas numpy
# 方案二（无 uv 时回退）：
# pip install matplotlib seaborn pandas numpy
```

---

## 注意事项

⚠️ **重要提示:**
1. 中文显示需配置字体: `plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS']`
2. 负号显示异常需: `plt.rcParams['axes.unicode_minus'] = False`
3. 大图用 `constrained_layout=True` 避免标签重叠
4. 保存前调用 `plt.savefig()`, 后用 `plt.show()`
5. 多子图用 `fig, axes = plt.subplots()` 而非多次 `plt.subplot()`
6. 金融图表建议深色背景: `plt.style.use('seaborn-v0_8-darkgrid')`
