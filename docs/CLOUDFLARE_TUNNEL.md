# Cloudflare Tunnel 公网入口

集群节点没有公网 IP，且不能安全地把共享节点绑定到 `0.0.0.0`。非 Docker 部署的推荐拓扑是：

```text
手机 HTTPS
    ↓
Cloudflare Named Tunnel（固定 hostname，可选 Cloudflare Access）
    ↓ 出站连接，不需要入站 80/443
cloudflared（必须与应用在同一个 Slurm 节点）
    ↓ http://127.0.0.1:8000
听见 native app
```

## 为什么不用 Quick Tunnel

Quick Tunnel 每次生成随机 hostname，没有稳定的 `APP_ORIGIN`、没有可用性承诺，只适合本次会话的临时演示。使用免访问码模式时仍需正确设置随机地址对应的 `APP_ORIGIN`，听见会精确校验 `Origin`、`Host`、CSRF 和 Secure Cookie；长期入口应使用 Named Tunnel。

正式入口使用 Cloudflare Zero Trust 中创建的 **Named Tunnel**，为固定 hostname 配置 Published application：

- Service URL：`http://127.0.0.1:8000`
- HTTP Host Header：固定公网 hostname，例如 `listen.example.com`
- WebSockets：开启
- `/api/*` 与 `/api/live`：绕过缓存，不使用会返回 Challenge HTML 的规则
- 对隐私要求高时增加 Cloudflare Access；也可以显式设置 `AUTH_REQUIRED=1` 启用听见的兼容访问码

Cloudflare 的代理连接有自己的超时边界；慢上传、长解码或单次推理必须用移动网络实测，必要时缩短录音或降低单次任务时长。当前应用每文件默认 50MB/10分钟，低于常见 100MB 上传上限，但不能把 Cloudflare 的边界当作应用成功保证。

不要把 Tunnel 指向会把 HTTP 重定向到 HTTPS 的 Caddy 入口，否则容易形成重定向循环。Tunnel 直连 app 时，TLS 在 Cloudflare 边缘终止，origin 只在 loopback 提供 HTTP。

## 在固定公网 hostname 上配置应用

先在同一个节点的项目目录执行：

```bash
python3 scripts/configure.py \
  --domain listen.example.com \
  --engine dual --tts on --accept-model-licenses
```

`APP_ORIGIN` 必须是最终公网地址（`https://listen.example.com`），不能保留 `localhost`。原生入口会拒绝开发配置并固定绑定 `127.0.0.1`。Cloudflare Tunnel 路径不使用 Caddy/ACME，`--email` 不需要填写。

默认免访问码部署不需要轮换访问码；若启用了兼容模式，当前目录若来自验收环境，上线前再执行一次 `python3 scripts/configure.py --set AUTH_REQUIRED=1 --rotate-code`，不要把验收访问码当成公网访问码。

```bash
module load ffmpeg/latest
python3 scripts/run_public_native.py --port 8000
```

把 Cloudflare 生成的 token 写进权限为 600 的文件。不要把 token 放在命令参数、`.env`、仓库、tmux 回滚屏或聊天记录：

```bash
umask 077
mkdir -p "$HOME/.config/tingjian"
chmod 700 "$HOME/.config/tingjian"
read -rsp 'Cloudflare tunnel token: ' token; echo
printf '%s' "$token" > "$HOME/.config/tingjian/cloudflared-token"
unset token
chmod 600 "$HOME/.config/tingjian/cloudflared-token"
```

在同一个 Slurm 作业、同一个节点的另一个 tmux pane 启动连接器：

```bash
export CLOUDFLARE_TUNNEL_TOKEN_FILE="$HOME/.config/tingjian/cloudflared-token"
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u NO_PROXY -u NODE_USE_ENV_PROXY \
  bash scripts/run_cloudflare_tunnel.sh
```

脚本会先检查 `/health/ready`，只接受 named tunnel token，不会偷偷退回 Quick Tunnel；默认使用 HTTP/2。当前集群 shell 可能继承只在 login01 存在的本地代理（例如 `127.0.0.1:7899`），启动 connector 时应按上面的命令清除这些变量；随后仍需观察 cloudflared 的 7844 出站连通性。若管理员只允许 UDP 出站，可在确认策略后设置 `CLOUDFLARE_TUNNEL_PROTOCOL=quic`。token 文件会被检查为 600 且不超过 4KB。

## 集群生命周期边界

`tmux` 不能延长 Slurm 作业。当前 `speech` 作业到期、被取消或节点重启时，应用和 Tunnel 会同时下线。长期服务需要管理员批准的服务节点/长时 QOS，或把 origin 与 connector 搬到专用 VPS/容器主机。不要为了跨节点连接而把应用改成 `0.0.0.0`。

上线前必须从移动网络验证：首次直接进入（或兼容模式登录）、麦克风权限、实时 WebSocket、停止时末句、上传、TTS、断线和页面切后台。Cloudflare 的边缘连接和集群节点的 job 存活都要单独监控。
