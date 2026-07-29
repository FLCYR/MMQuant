#!/usr/bin/env bash
# 一键部署：本地改完代码后跑这个脚本，同步到云服务器并重启服务。
# 用法：bash scripts/deploy.sh
#
# 前提：本机 SSH key 已加入服务器 root 的 authorized_keys（部署时已配好，
# 无需密码）；服务器上 /var/www/mmquant 是本仓库的 git clone。
set -euo pipefail

SERVER="root@8.134.192.201"
REMOTE_DIR="/var/www/mmquant"

cd "$(dirname "$0")/.."

echo "==> 推送代码到 GitHub"
git push

echo "==> 本地打包前端"
(cd frontend && npm run build)

echo "==> 同步前端静态文件到服务器"
tar -czf /tmp/mmquant_dist.tar.gz -C frontend dist
scp /tmp/mmquant_dist.tar.gz "$SERVER:$REMOTE_DIR/frontend/mmquant_dist.tar.gz"
rm -f /tmp/mmquant_dist.tar.gz

echo "==> 服务器拉取代码 + 解压前端 + 重启服务"
ssh "$SERVER" "
set -e
cd $REMOTE_DIR
git pull
cd frontend && rm -rf dist && tar -xzf mmquant_dist.tar.gz && rm mmquant_dist.tar.gz && cd ..
venv/bin/pip install -r requirements.txt -q
systemctl restart mmquant-api mmquant-scheduler
systemctl --no-pager status mmquant-api mmquant-scheduler | grep -E 'Active|●'
"

echo "==> 完成：http://8.134.192.201:8080"
