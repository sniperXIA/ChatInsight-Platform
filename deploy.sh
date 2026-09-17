#!/usr/bin/env bash
# ==============================================================================
# ChatInsight Platform - Ubuntu Cloud Server Deployment Script
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================================="
echo "🚀 开始部署 ChatInsight Platform 至 Ubuntu 云服务器"
echo "=================================================================="

# 1. 检查 Docker & Docker Compose
if ! command -v docker &> /dev/null; then
    echo "⚙ 未安装 Docker，正在通过官方脚本安装..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER" || true
fi

# 2. 检查 .env
if [ ! -f ".env" ]; then
    echo "⚙ 未检测到 .env 配置文件，正在从 .env.example 复制模板..."
    cp .env.example .env
    echo "💡 请根据实际云服务器环境与密钥编辑 .env 文件后重新运行 ./deploy.sh"
fi

# 3. 创建持久化数据目录
mkdir -p data/cache data/uploads logs

# 4. 构建并启动 Docker 容器
echo "⚙ 正在构建并拉起 Docker 服务..."
docker compose build --pull
docker compose up -d

# 5. 验证服务健康状态
echo "⏳ 等待服务启动健康检查..."
sleep 5

if curl -s http://127.0.0.1:8000/api/v1/health | grep -q "ok"; then
    echo "=================================================================="
    echo "🎉 ChatInsight Platform 部署成功！"
    echo "   * 服务地址: http://<你的服务器IP>:8000/"
    echo "   * API 文档: http://<你的服务器IP>:8000/docs"
    echo "   * 查看运行日志: docker compose logs -f"
    echo "   * 停止服务: docker compose down"
    echo "=================================================================="
else
    echo "⚠️ 容器已启动，但健康检查未能立即返回成功，请使用 'docker compose logs' 查看日志排查。"
fi
