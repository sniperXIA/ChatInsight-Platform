#!/usr/bin/env bash
# ==============================================================================
# ChatInsight Platform - macOS / Local Development Quickstart Script
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 🚀 正在启动 ChatInsight Platform 本地服务 (macOS/Linux) ==="

# 1. 检查 Python 环境
if [ -d ".venv" ]; then
    echo "✔ 发现虚拟环境 .venv"
    source .venv/bin/activate
elif command -v uv &> /dev/null; then
    echo "⚙ 使用 uv 创建 Python 3.11 虚拟环境..."
    uv venv --python 3.11 .venv
    source .venv/bin/activate
    uv pip install -r requirements.txt
else
    echo "⚙ 使用 python3 创建虚拟环境..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
fi

# 2. 检查 .env 配置文件
if [ ! -f ".env" ]; then
    echo "⚙ 未检测到 .env 配置文件，正在从 .env.example 生成默认配置..."
    cp .env.example .env
fi

# 3. 检查 FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️ 警告: 未检测到 ffmpeg 命令，视频帧截取与多模态解析可能受限。"
    echo "   macOS 请执行: brew install ffmpeg"
else
    echo "✔ FFmpeg 已就绪: $(ffmpeg -version | head -n 1)"
fi

# 4. 启动 FastAPI Web 服务 & UI Dashboard
PORT=${CI_PORT:-8000}
HOST="0.0.0.0"

echo "=================================================================="
echo "🎉 ChatInsight Platform Web 仪表盘已就绪!"
echo "   * 浏览器访问入口: http://localhost:${PORT}/"
echo "   * 交互式 API 文档: http://localhost:${PORT}/docs"
echo "   * 按 Ctrl+C 可停止服务"
echo "=================================================================="

exec uvicorn apps.api.main:app --host "$HOST" --port "$PORT" --reload
