# -*- coding: utf-8 -*-
# 物流企业碳感知智能决策系统 · 企业级容器镜像
#
# 构建：docker build -t carbontide:latest .
# 运行：docker compose --env-file deploy/env up -d
#
# 纪律：
#   1. 密钥（config/*_key.txt）不 COPY 进镜像 —— 运行时用环境变量或只读挂载注入；
#   2. 原始数据（data/raw/）不 COPY 进镜像 —— 由 .dockerignore 挡住；
#   3. 非 root 运行 —— data/ 与 outputs/ 归 app 用户，可写。
FROM python:3.12-slim

LABEL org.opencontainers.image.title="carbon-sense-decision" \
      org.opencontainers.image.description="物流企业碳感知智能决策系统（Streamlit 决策台 + Flask 传感器网关）" \
      org.opencontainers.image.licenses="UNLICENSED"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    HOME=/home/app \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

# ---- 系统依赖 -------------------------------------------------------
#   ca-certificates  ：出网 HTTPS（高德 REST / DeepSeek）需要信任链
#   fontconfig + CJK  ：中文渲染（图表/导出/无头浏览器场景）
#   libstdc++6       ：pyarrow / numpy 的运行期依赖（slim 精简过）
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        fontconfig \
        fonts-noto-cjk \
        libstdc++6; \
    rm -rf /var/lib/apt/lists/*; \
    fc-cache -f || true

WORKDIR /app

# ---- 依赖层（与源码分离，改代码不重装依赖）-----------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---- 运行用户（非 root）----------------------------------------------
RUN set -eux; \
    groupadd --gid 1000 app; \
    useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app; \
    mkdir -p /app/data/processed /app/outputs /app/backups

# ---- 源码与只读配置 ---------------------------------------------------
# config/ 只取因子 JSON 与说明文档；*.txt 密钥文件不进镜像
COPY app/ ./app/
COPY src/ ./src/
COPY tools/ ./tools/
COPY config/*.json ./config/
COPY config/README.md ./config/
COPY templates/ ./templates/
COPY .streamlit/ ./.streamlit/
COPY server.py ./server.py

# ---- 结果表（32 张）：镜像内自带一份，便于开箱即用 ----------------------
# 运行时 compose 会把宿主 ./data 挂到 /app/data，覆盖这一份；
# 宿主 data/processed 必须存在，否则页面读不到结果表（见 deploy/README.md）。
COPY data/processed/ ./data/processed/

# ---- 属主归还给运行用户 -----------------------------------------------
RUN chown -R app:app /app /home/app

USER app

EXPOSE 8501 5000

# ---- 健康检查：只用 Python 标准库，slim 镜像里没有 curl -----------------
HEALTHCHECK --interval=30s --timeout=6s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health',timeout=4).status==200 else 1)" 2>/dev/null || exit 1

# ---- 默认启动：Streamlit 决策台 ----------------------------------------
CMD ["python", "-m", "streamlit", "run", "app/app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
