#!/usr/bin/env bash
set -Eeuo pipefail

# Named-tunnel connector for the non-Docker deployment. Quick Tunnels are
# intentionally not supported here: their random hostname cannot match the
# app's exact Origin/Host/CSRF configuration and has no uptime guarantee.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIGIN="${TINGJIAN_ORIGIN:-http://127.0.0.1:8000}"
# Keep the default on the shared home filesystem. Slurm's XDG_RUNTIME_DIR is
# node-local and may disappear when a job or connector is restarted.
TOKEN_FILE="${CLOUDFLARE_TUNNEL_TOKEN_FILE:-${HOME}/.config/tingjian/cloudflared-token}"
PROTOCOL="${CLOUDFLARE_TUNNEL_PROTOCOL:-http2}"

if ! command -v cloudflared >/dev/null 2>&1; then
  if [[ -x "${HOME}/.local/bin/cloudflared" ]]; then
    CLOUDFLARED_BIN="${HOME}/.local/bin/cloudflared"
  else
    echo '找不到 cloudflared；请先安装 Cloudflare 官方 Linux 版本。' >&2
    exit 127
  fi
else
  CLOUDFLARED_BIN="$(command -v cloudflared)"
fi

if [[ ! -f "$TOKEN_FILE" || ! -s "$TOKEN_FILE" ]]; then
  echo "缺少 named tunnel token 文件: $TOKEN_FILE" >&2
  echo '请用权限 600 的 secret 文件提供 token；不会回退到 Quick Tunnel。' >&2
  exit 2
fi
token_mode="$(stat -c '%a' "$TOKEN_FILE" 2>/dev/null || stat -f '%Lp' "$TOKEN_FILE")"
if [[ "$token_mode" != "600" ]]; then
  echo "token 文件权限过宽（当前 $token_mode），请 chmod 600: $TOKEN_FILE" >&2
  exit 2
fi
if [[ "$(wc -c < "$TOKEN_FILE")" -gt 4096 ]]; then
  echo 'token 文件异常过大，拒绝读取。' >&2
  exit 2
fi

if [[ "$ORIGIN" != http://127.0.0.1:* && "$ORIGIN" != http://localhost:* ]]; then
  echo "Tunnel origin 必须是本机 HTTP loopback 地址，当前为: $ORIGIN" >&2
  exit 2
fi
if ! curl --silent --show-error --fail --connect-timeout 3 --max-time 8 "$ORIGIN/health/ready" \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ready") is True else 1)'; then
  echo "应用未就绪: $ORIGIN/health/ready" >&2
  exit 1
fi

echo "启动 named Cloudflare Tunnel；origin=$ORIGIN；protocol=$PROTOCOL" >&2
exec "$CLOUDFLARED_BIN" tunnel \
  --no-autoupdate \
  --protocol "$PROTOCOL" \
  --edge-ip-version 4 \
  --metrics 127.0.0.1:0 \
  run --token-file "$TOKEN_FILE"
