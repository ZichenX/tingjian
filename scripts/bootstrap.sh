#!/usr/bin/env bash
# Run on a dedicated Ubuntu 24.04/22.04 or Debian 12/13 server, as root.
set -Eeuo pipefail
trap 'echo "安装在第 $LINENO 行失败；未完成的步骤不能视为成功。" >&2' ERR
[[ ${EUID} -eq 0 ]] || { echo "请使用 sudo bash scripts/bootstrap.sh" >&2; exit 1; }
source /etc/os-release
case "${ID}:${VERSION_CODENAME}" in ubuntu:noble|ubuntu:jammy|debian:bookworm|debian:trixie) ;; *) echo "此脚本仅支持 Ubuntu 22.04/24.04、Debian 12/13。" >&2; exit 1 ;; esac
arch=$(dpkg --print-architecture)
[[ "$arch" == "amd64" || "$arch" == "arm64" ]] || { echo "需要 64 位 amd64/arm64 主机"; exit 1; }
apt-get update
apt-get install -y ca-certificates curl python3 unzip
if ! command -v docker >/dev/null; then
  for package in docker.io podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      echo "检测到现有 $package。为避免破坏其他服务，请按 Docker 官方文档手动处理冲突后再运行。" >&2; exit 1
    fi
  done
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --location --retry 5 "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' "$arch" "$ID" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker info >/dev/null
docker compose version
cat <<'MSG'
基础环境已就绪。脚本不会自动修改防火墙、SSH 或云安全组。
请在云安全组开放 TCP 80/443；SSH 端口仅向管理员 IP 开放。
Docker 管理权限等同 root 权限。不要把 Docker socket 暴露公网。
MSG
