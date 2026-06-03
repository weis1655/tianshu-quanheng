# 天枢权衡 - Dockerfile
# 多阶段构建，优化镜像大小

# ============ 阶段1: 构建 ============
FROM python:3.11-slim as builder

WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ============ 阶段2: 运行 ============
FROM python:3.11-slim

# 标签
LABEL maintainer="天枢权衡"
LABEL version="3.0"
LABEL description="天枢权衡 - AI驱动的股票分析系统"

# 设置工作目录
WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 复制应用代码
COPY . .

# 创建必要目录
RUN mkdir -p logs data/cache data/reports data/metrics

# 健康检查
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python health.py || exit 1

# 默认命令
CMD ["python", "main.py", "--help"]