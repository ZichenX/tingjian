#!/usr/bin/env bash
# Small single-host deployment. It deliberately stops old services for the real model acceptance gate.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
trap 'printf "\n部署未完成（步骤/行 %s）。未声称服务成功。请运行 bash scripts/doctor.sh；不要分享 .env 或访问码。\n" "$LINENO" >&2' ERR
command -v docker >/dev/null || { echo '请先 sudo bash scripts/bootstrap.sh'; exit 1; }
docker info >/dev/null 2>&1 || { echo '当前用户无法访问 Docker。请使用 sudo，或在理解 Docker 组等同 root 权限后配置权限。'; exit 1; }
docker compose version >/dev/null
[[ -f .env ]] || { echo '先运行 python3 scripts/configure.py --domain ... --email ...'; exit 1; }
[[ "$(python3 scripts/configure.py --get MODEL_LICENSES_ACK)" == '1' ]] || { echo '请先阅读并确认 MODEL_LICENSES.md'; exit 1; }
DOMAIN=$(python3 scripts/configure.py --get DOMAIN)
ORIGIN=$(python3 scripts/configure.py --get APP_ORIGIN)
[[ "$ORIGIN" == https://* && "$DOMAIN" != localhost ]] || { echo 'deploy.sh 只接受公网 HTTPS 配置；本地开发请见文档'; exit 1; }
OFFLINE=()
[[ "${1:-}" == --offline-models ]] && OFFLINE=(--offline)
if [[ -n "${1:-}" && "${1:-}" != --offline-models ]]; then echo '用法: bash scripts/deploy.sh [--offline-models]'; exit 1; fi
docker compose config --quiet
mkdir -p models
printf '\n[1/6] 构建应用镜像。模型不写入镜像。\n'
# Preserve the last built image; for a validated release also back up its code/.env and model lock.
if docker image inspect tingjian:local >/dev/null 2>&1; then docker tag tingjian:local tingjian:previous; fi
docker compose build --pull app
printf '\n[2/6] 下载/校验选中的模型。可以安全重复执行。\n'
docker compose run --rm --no-deps tools python scripts/download_models.py "${OFFLINE[@]}"
printf '\n[3/6] 检查 Caddy 配置。\n'
docker compose pull caddy
docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
printf '\n[4/6] 进入维护窗口，停止旧实例，避免两份模型同时占用内存。\n'
docker compose stop caddy app
# No fake data path. Failure leaves services stopped instead of exposing a broken instance.
docker compose run --rm --no-deps tools timeout --signal=TERM --kill-after=15s 660s python scripts/self_test.py
printf '\n[5/6] 启动并等待模型就绪。\n'
docker compose up -d --wait --wait-timeout 600 app caddy
printf '\n[6/6] 从当前服务器验证公网域名与可信 HTTPS。\n'
OK=0
for attempt in $(seq 1 12); do
  if curl --silent --show-error --fail --connect-timeout 10 --max-time 15 "$ORIGIN/health/ready" 2>/dev/null | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ready") is True else 1)' 2>/dev/null; then OK=1; break; fi
  sleep 5
done
[[ "$OK" == 1 ]] || { echo '容器可能已启动，但公网 HTTPS 验证未通过。检查 DNS、错误 AAAA、80/443 安全组和 Caddy 日志。'; exit 1; }
printf '\n服务与真实模型基础验收通过：%s\n' "$ORIGIN"
printf '下一步必须用手机移动网络访问，允许麦克风，测试说话、停止后的最后一句及普通话朗读。\n'
printf '模型验收报告与朗读样例位于 models/acceptance/；样例通过不等于山东话准确率达标。\n'
