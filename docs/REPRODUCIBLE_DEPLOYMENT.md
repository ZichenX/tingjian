# 可复现部署手册

这份手册给出从 GitHub 源码到可访问服务的完整顺序。它把“代码可以启动”“真实模型已经加载”“公网浏览器可以使用”分成三个独立检查点，避免只看到容器启动就误认为语音识别已经可用。

## 0. 选择入口

| 条件 | 使用的入口 | 必须满足 |
| --- | --- | --- |
| 有专用公网主机并能管理 DNS、80/443 和 Docker | Docker + Caddy | 域名 A/AAAA、云安全组和主机防火墙正确；不要让其他服务占用 80/443 |
| 计算集群没有入站端口，但允许出站 HTTPS/HTTP2/QUIC | Cloudflare Named Tunnel | 固定 hostname、Named Tunnel token、app 与 connector 在同一个节点和同一个 Slurm 作业 |
| 只有临时演示需求 | Quick Tunnel | 不在本项目生产路径中；随机 hostname 不适合固定 `APP_ORIGIN`、访问码和长期可用性 |

两种正式入口都让应用只监听本机/内部网络。不要为了解决访问问题把应用绑定到 `0.0.0.0`，也不要把 8000 直接暴露公网。

## 1. 固定源码并检查主机

```bash
git clone https://github.com/ZichenX/tingjian.git
cd tingjian
git checkout 1.0.0-rc1        # 需要可重复发布时固定版本
git rev-parse HEAD             # 记录到部署单
```

Docker 主机的建议起点是 Ubuntu 24.04 amd64、8 vCPU、16 GB RAM、40 GB 空闲磁盘。模型和容器会占用较多内存；这不是吞吐保证。先检查：

```bash
uname -m
free -h
df -h .
sudo ss -ltnp | grep -E ':80 |:443 |:8000 ' || true
```

专用主机还需要管理员能够修改 DNS、云安全组和主机防火墙。集群路径不需要入站端口，但必须确认 Slurm 作业在 connector 运行期间不会结束。

## 2. 配置访问码和域名

先阅读 [MODEL_LICENSES.md](../MODEL_LICENSES.md)。`configure.py` 不执行 shell，不把明文访问码写入命令行；`.env` 会以 600 权限原子写入：

```bash
python3 scripts/configure.py \
  --domain listen.example.com \
  --email admin@example.com \
  --engine dual \
  --tts on \
  --accept-model-licenses
```

交互式命令会隐式询问访问码；留空会生成一次性显示的 12 位数字码。把它存放在管理员密码管理器中，不要写进 README、工单、shell 历史或 URL。

无交互环境使用权限为 600 的临时文件：

```bash
umask 077
printf '%s\n' '替换为你自己的至少12位访问码' > /run/tingjian-code
chmod 600 /run/tingjian-code
python3 scripts/configure.py \
  --domain listen.example.com \
  --email admin@example.com \
  --code-file /run/tingjian-code \
  --engine dual --tts on --accept-model-licenses
rm -f /run/tingjian-code
```

不要在公网部署中使用 `--dev`。部署前确认：

```bash
stat -c '%a %n' .env       # 应为 600
python3 scripts/configure.py --get DOMAIN
python3 scripts/configure.py --get APP_ORIGIN
```

## 3. 路径 A：Docker + Caddy

### 3.1 安装 Docker

只在专用 Ubuntu/Debian 主机运行：

```bash
sudo bash scripts/bootstrap.sh
```

脚本不会改 SSH、防火墙、云安全组或其他服务。随后在 DNS 中把域名 A 记录指向主机公网 IPv4；只有确认 IPv6 可达时才添加 AAAA。云安全组和主机防火墙允许 TCP 80/443，8000 不允许公网进入。

### 3.2 构建、下载、真实模型验收并启动

```bash
sudo bash scripts/deploy.sh
```

脚本顺序固定为：

1. 构建不含模型的应用镜像；
2. 下载并校验当前模式需要的模型；
3. 校验 Caddy 配置；
4. 停止旧实例，避免两份模型同时占用内存；
5. 执行真实 VAD/ASR/TTS `scripts/self_test.py`；
6. 启动 app 和 Caddy，等待 `/health/ready`；
7. 使用可信 HTTPS 检查最终域名。

任何一步失败都返回非零状态。模型验收报告和生成的 TTS WAV 在 `models/acceptance/`，它们是运行时产物，不提交到 Git。

网络受限但模型压缩包已放入 `models/.cache/` 时：

```bash
sudo bash scripts/deploy.sh --offline-models
```

离线模式只跳过模型下载；Docker 基础镜像、PyPI 依赖、Caddy 镜像和证书申请仍需要各自的网络或预置缓存。

### 3.3 验证和日常维护

```bash
sudo bash scripts/doctor.sh
sudo docker compose ps
sudo docker compose logs --tail=80 app caddy
```

从手机移动网络而非服务器或同一 Wi-Fi 完成 [目标环境验收单](ACCEPTANCE.md)。至少验证登录、麦克风授权、实时听写停止时的最后一句、录音上传、TTS、断网和切后台。

改变 `.env` 后必须重新执行 `sudo bash scripts/deploy.sh`；单独 `docker compose restart` 不会重新读取新的 Compose 环境变量。部署脚本会短暂停机，不是零停机发布。

## 4. 路径 B：Cloudflare Named Tunnel + 原生进程

这条路径适合登录节点/计算节点不能接受入站连接的集群。它不运行 Caddy，也不需要开放 80/443；TLS 在 Cloudflare 边缘终止，connector 通过出站连接回到同一节点的 `127.0.0.1:8000`。

### 4.1 准备固定 Tunnel

在 Cloudflare Zero Trust 中创建 Named Tunnel 和固定 hostname，Published application 指向：

```text
Service: http://127.0.0.1:8000
WebSockets: enabled
HTTP Host Header: 固定公网 hostname
```

`APP_ORIGIN` 必须是最终的 `https://固定hostname`。不要使用随机 Quick Tunnel hostname，也不要把 token 放在命令参数、`.env`、tmux 回滚屏或聊天记录。

### 4.2 在同一节点准备模型和应用

以下命令在**同一个 Slurm 作业、同一个节点、项目目录**执行。`download_models.py` 和 `self_test.py` 需要读取 `.env` 中的环境变量；只对由 `configure.py` 生成的本地文件使用下面的 `source`：

```bash
set -a
. ./.env
set +a

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/download_models.py
python scripts/self_test.py
```

若已经有管理员提供的依赖环境，可以跳过虚拟环境创建，但仍需确保 Python、FFmpeg、`sherpa-onnx==1.12.40` 和 `cloudflared` 可用。真实 `self_test.py` 必须返回 0，不能用 `tests/` 的 fake 引擎替代。

### 4.3 安全保存 token 并启动

```bash
umask 077
mkdir -p "$HOME/.config/tingjian"
chmod 700 "$HOME/.config/tingjian"
read -rsp 'Cloudflare tunnel token: ' token; echo
printf '%s' "$token" > "$HOME/.config/tingjian/cloudflared-token"
unset token
chmod 600 "$HOME/.config/tingjian/cloudflared-token"
```

在 tmux 的一个窗口启动 app：

```bash
set -a; . ./.env; set +a
. .venv/bin/activate
python scripts/run_public_native.py --port 8000
```

另一个窗口启动 connector。集群 shell 若继承了只在 login 节点可用的本地代理，先清除这些变量：

```bash
export CLOUDFLARE_TUNNEL_TOKEN_FILE="$HOME/.config/tingjian/cloudflared-token"
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u NO_PROXY -u NODE_USE_ENV_PROXY \
  bash scripts/run_cloudflare_tunnel.sh
```

脚本只接受权限为 600、大小不超过 4 KB 的 Named Tunnel token，并在启动前检查本地 `/health/ready`；失败时不会回退到 Quick Tunnel。

### 4.4 集群生命周期

tmux 只能保持作业内进程，不会延长 Slurm 作业。作业到期、被取消、节点重启或文件系统不可用时，公网服务会同时下线。长期服务需要管理员批准的长时 QOS/服务节点，或将 origin 和 connector 搬到专用主机。详见 [Cloudflare Tunnel 说明](CLOUDFLARE_TUNNEL.md)。

## 5. 模型完整性和严格供应链模式

默认下载器会把压缩包 SHA256 和解压文件清单写入 `models/artifacts.lock.json`。这是本地内容锁；第一次下载的来源信任是 TOFU，不等同于上游签名。管理员可以从独立渠道确认官方压缩包 SHA256：

```bash
sudo docker compose run --rm --no-deps \
  -v "$PWD/trusted-checksums.json:/trusted/checksums.json:ro" \
  tools python scripts/download_models.py \
  --checksums /trusted/checksums.json --require-trusted-hashes
```

可信文件格式是 `{ "官方压缩包文件名": "64位小写或大写SHA256" }`。不要把未经核实的哈希填进这个文件来制造“已验证”假象。

## 6. 回滚、轮换和清理

- 发布失败：先保留 `docker compose logs`、`models/acceptance/` 和镜像 ID；不要删除证据后重试。
- Docker 部署会把上一次镜像标为 `tingjian:previous`，但模型目录由当前 lock 管理。回滚前确认代码、`.env` 和模型版本匹配，再由管理员执行 `docker compose up -d`。
- 访问码泄露：运行 `python3 scripts/configure.py --rotate-code`，它会同时轮换会话签名密钥；随后重新部署，旧 Cookie 失效。
- 访问码只在首次配置或轮换时显示一次；不要从日志中寻找它。
- 删除模型缓存前确认没有正在运行的 app/self-test；用户录音不应写入仓库或公开目录。

## 7. 部署记录模板

建议把以下信息保存到管理员私有运维记录，不要提交到公开仓库：

```text
源码 commit/tag:
主机系统与架构:
CPU/内存/磁盘:
部署路径（Docker/Caddy 或 Named Tunnel）:
ASR_MODE / TTS_ENABLED:
模型 artifacts.lock.json 摘要:
镜像 ID（Docker 路径）:
self_test 报告路径与时间:
/health/ready 验证时间:
移动网络人工验收结果:
未通过项目与补救计划:
```

只有真实模型自检、可信 HTTPS 和手机验收都完成后，才可以把实例标记为对外可用。模型样例输出非空不代表方言 CER、并发能力或手机录音质量已经达标。
