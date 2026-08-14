#!/usr/bin/env bash
# 更新 coc-cards：git pull → 重建 → 等健康檢查通過。
# 用法：在任何位置執行 ./update.sh
set -euo pipefail
cd "$(dirname "$0")"

# --ff-only：只做安全的快轉。本地跟遠端分岔就直接失敗中止，不做破壞性操作。
git pull --ff-only

if [[ ! -f .env ]]; then
    echo "錯誤：缺少 .env（需要 COC_API_KEY）。可從 .env.example 複製。" >&2
    exit 1
fi

docker compose up -d --build

# 等健康檢查。CoC API key 綁 IP，key 過期或 IP 變動時容器起得來但功能全壞，
# 所以這裡不只看容器在不在，還要真的打一次 /healthz。
echo -n "等待服務就緒"
for _ in $(seq 1 30); do
    if docker compose exec -T coc-cards \
        python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3848/healthz')" \
        2>/dev/null; then
        echo " 完成"
        docker image prune -f
        exit 0
    fi
    echo -n "."
    sleep 2
done

echo " 逾時" >&2
docker compose logs --tail 50 coc-cards >&2
exit 1
