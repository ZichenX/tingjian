#!/usr/bin/env bash
# Diagnostics deliberately do NOT dump .env, cookies, transcripts or full compose config.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
echo '=== 系统 ==='
uname -sm
command -v free >/dev/null && free -h
command -v df >/dev/null && df -h .
echo '=== Docker ==='
docker version --format '{{.Server.Version}}' 2>/dev/null || true
docker compose version 2>/dev/null || true
docker compose ps 2>/dev/null || true
echo '=== 域名 ==='
DOMAIN=$(python3 scripts/configure.py --get DOMAIN 2>/dev/null || true)
ORIGIN=$(python3 scripts/configure.py --get APP_ORIGIN 2>/dev/null || true)
if [[ -n "$DOMAIN" ]]; then
  printf '域名: %s\n' "$DOMAIN"
  getent ahosts "$DOMAIN" 2>/dev/null || true
fi
echo '=== 端口（宿主机不应公开 8000） ==='
ss -ltn 2>/dev/null | grep -E ':80 |:443 |:8000 ' || true
echo '=== HTTPS 就绪检查（不跳过证书验证） ==='
[[ -n "$ORIGIN" ]] && curl -sS --connect-timeout 10 --max-time 15 "$ORIGIN/health/ready" || true
printf '\n=== 容器健康 ===\n'
ID=$(docker compose ps -q app 2>/dev/null || true)
[[ -n "$ID" ]] && docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}} OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}' "$ID" || true
printf '\n继续排查: docker compose logs --tail=80 app caddy\n分享日志前仍需自行检查域名/IP/敏感路径；不要分享 .env。\n'
