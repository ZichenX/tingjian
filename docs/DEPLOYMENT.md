# 部署、维护与故障排查

## 支持范围与前置检查

主路径：专用 Linux 主机、Docker Engine + Compose v2、域名直连 Caddy。首轮推荐 Ubuntu 24.04 amd64 / 8 vCPU / 16GB RAM / 40GB 空闲磁盘。脚本同时识别 Ubuntu 22.04、Debian 12/13 和 arm64，但**这里没有对这些系统/架构逐一构建实测**。不要据此把低功耗 ARM 小板认作等效服务器。以目标服务器真实自检为准。

运行前：

```bash
uname -m
free -h
df -h .
sudo ss -ltnp | grep -E ':80 |:443 |:8000 ' || true
```

专用主机可按 README 一次部署。有现存 Nginx/其他网站时，不要让脚本强行接管端口。由管理员合并反代、证书与 WS 配置，重新审核代理信任边界；当前默认 Compose 只覆盖“Caddy 直接面对公网”，不自动配置 CDN 或多级反代。

### 集群无入站端口时的 Cloudflare Tunnel 路径

集群节点没有公网 IP 或无法管理 80/443 时，使用 [Cloudflare Tunnel 部署说明](CLOUDFLARE_TUNNEL.md)：固定 hostname 的 Named Tunnel → 同一 Slurm 节点上的 `127.0.0.1:8000`。此路径不运行 Compose/Caddy，必须使用 `scripts/run_public_native.py`，并让 `cloudflared` 与 app 位于同一节点和同一作业。Quick Tunnel 只适合短时演示，不是生产入口。

域名 A 指向正确公网 IP；AAAA 只有在 IPv6 确实可达时设置。云安全组与宿主机防火墙都要允许 TCP 80/443。SSH 只向管理员范围开放，保留现有 SSH 通道，不盲目重置防火墙。浏览器使用 `https://你的域名`，不是 HTTP 裸 IP。

## 配置原则

`configure.py` 只读写简单 KEY=VALUE，不执行 shell；`.env` 权限 600。不要 `source` 不可信 `.env`，不要把明文访问码作为命令参数，不要提交 `.env`。首次无需手工复制 `.env.example`；配置脚本会自动加载默认值。

**同一个管理员用户管理源代码和 .env；Docker 操作用 sudo 即可。** 如果曾用 sudo 生成 `.env` 而当前用户无法读取，需要用同一账户管理或谨慎修正文件所有者，不要改成 644 向所有人开放。

无人值守部署可使用安全注入的临时访问码文件：

```bash
# /run/tingjian-code 由你的密钥系统/管理员创建；不要把真实访问码写进仓库。
sudo chmod 600 /run/tingjian-code
sudo python3 scripts/configure.py \
  --domain listen.example.com --email admin@example.com \
  --code-file /run/tingjian-code --engine dual --tts on --accept-model-licenses
sudo rm /run/tingjian-code
sudo bash scripts/deploy.sh
```

不要把这段示例里的域名当作真实站点。脚本无法替代 DNS/云控制台权限。

## 运行参数

使用 `python3 scripts/configure.py --set KEY=VALUE` 更改；之后 `sudo bash scripts/deploy.sh` 才会使容器使用新配置。`docker compose restart` **不会**重新加载新 `.env` 环境变量。

| 参数 | 默认 | 含义/边界 |
|---|---:|---|
| `ASR_MODE` | dual | 用 `--engine` 切换 |
| `TTS_ENABLED` | 1 | 用 `--tts on/off` |
| `ASR_THREADS` | 3 | native ASR 内部线程；不是用户数 |
| `APP_MEMORY` | 12g | 应用/工具容器内存上限；为系统/Caddy 保留内存 |
| `MAX_LIVE_SESSIONS` | 2 | 会话准入上限，不是实时吞吐保证；每公网 IP 仍最多一路 |
| `MAX_UPLOAD_MB` | 50 | 每文件体积；最多可设置 100；Caddy 全局上限 100MB |
| `MAX_UPLOAD_SECONDS` | 600 | 每文件解码时长；最大 1800 秒 |
| `MAX_SESSION_SECONDS` | 1800 | 每次实时听写最长 30 分钟；到时定稿、需重新开始 |
| `COOKIE_DAYS` | 30 | 登录有效期；共享设备可设更短 |
| `VAD_SILENCE` | 0.7 | 语音停顿阈值；0.3～2 秒 |
| `SEGMENT_SECONDS` | 12 | 连续讲话强制切段目标；4～25 秒 |
| `PARTIAL_INTERVAL` | 1.8 | 预览最小间隔；1～10 秒；繁忙时可跳过 |
| `INFERENCE_TIMEOUT` | 120 | 原生推理超时，强制重启应用；30～600 秒 |
| `NANO_HOTWORDS` | 空 | 仅 Fun-ASR-Nano；最多 1000 字符；不改其他模型 |
| `MODEL_BASE_URL` | 空 | 仅管理员控制并核实的 HTTPS 缓存源，详见下文 |

小主机先单 CTC、关 TTS、单路，运行真实自检并看峰值内存/RTF，再决定增加功能。不要凭模型文件大小推算精确内存；不要把单条样例 RTF 当作并发服务性能。

## 下载、重试、离线缓存与完整性

正常路径 `deploy.sh` 自动下载**所选模式**所需文件。网络中断保留 `.part`，重跑可续传；支持重试和 HTTPS。压缩包只允许固定顶层目录下的常规文件/目录，拒绝路径穿越、软链接/硬链接和超限解压。完整解压校验后才替换旧目录。

`models/artifacts.lock.json` 是生成的内容锁；以后重复执行会检查解压文件 SHA256。第一次记录为 HTTPS+TOFU，不是假装存在已核实的上游哈希。可由管理员在独立可信渠道确认压缩包哈希，提供：

```json
{
  "silero_vad.onnx": "请填写真实的64位SHA256，不要使用此占位值"
}
```

严格模式需所选全部文件均有可信哈希：

```bash
sudo docker compose run --rm --no-deps \
  -v "$PWD/trusted-checksums.json:/trusted/checksums.json:ro" \
  tools python scripts/download_models.py \
  --checksums /trusted/checksums.json --require-trusted-hashes
```

**GitHub 访问受限时**，在有权限、可访问官方源的机器下载同名文件，复制到服务器 `models/.cache/`。默认 dual+TTS 的固定清单如下，不需要解压：

```text
silero_vad.onnx
sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25.tar.bz2
sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26.tar.bz2
vits-melo-tts-zh_en.tar.bz2
```

官方 URL 在 `app/catalog.py`，统一来自 sherpa-onnx 的 `asr-models` / `tts-models` release。

```bash
# 模型不访问网络；但 Docker 基础镜像、apt/PyPI 构建和证书申请仍需要网络。
sudo bash scripts/deploy.sh --offline-models
```

`--offline-models` **不是整个系统完全离线安装**。真正隔离网络需要管理员预先构建/加载受信任 Docker 镜像及模型、管理可用的可信证书，并调整构建流程。公网手机访问仍需要网络。

管理员自建 HTTPS 镜像必须按 `/asr-models/文件名`、`/tts-models/文件名` 保存原始文件，再设置 `MODEL_BASE_URL=https://可信镜像/基础路径`。不是任意 Hugging Face/ModelScope 根域名都符合此布局；不要猜地址，也不要 `curl -k`。镜像应配独立可信哈希。

校验失败：先停止服务，确认是否是磁盘损坏、误修改或上游变更。不要删整个 lock 使变化被静默接受。确认来源后，备份 lock，仅移除需重新安装的条目和对应固定模型目录/缓存，并重新安装和真实验收；保存变更记录。

## 真实自检与超时

`deploy.sh` 会先进入维护窗口，防止旧服务和自检同时常驻两份大模型。自检运行在独立工具容器：Python 超时门槛 600 秒，外层 GNU timeout 660 秒并带强制终止，避免原生函数卡住后无限等待。

```bash
# 已停止 app 后单独诊断，避免内存叠加。
sudo docker compose stop caddy app
sudo docker compose run --rm --no-deps tools \
  timeout --signal=TERM --kill-after=15s 660s python scripts/self_test.py
```

模型包自带测试 WAV 优先使用；需替代样例时，可将经授权的短语音放 `models/custom-test.wav` 并传 `--sample /models/custom-test.wav`。自检报告会记录这条样例的文字，勿将敏感私人样例报告公开。

自检不评价山东话准确率，不保证浏览器麦克风正常，不做并发压测。得到模型报告后仍要执行 `docs/ACCEPTANCE.md`。

## 更新、备份与回滚

发布前备份**上一套源代码、私密 .env、模型 lock、通过验收的报告与镜像 ID**。`.env` 含签名密钥，只能加密/受控存储。Caddy 的 `tingjian_caddy_data` 卷保存私钥与证书，备份也必须私密。不要执行 `docker compose down -v` 作为常规重启。

```bash
sudo docker image inspect tingjian:local --format '{{.Id}}'
sudo docker compose exec -T app cat /opt/runtime-installed.txt
# 保存镜像到安全位置；不要把环境文件混进公开下载包。
sudo docker image save tingjian:local | gzip > /安全备份目录/tingjian-accepted-image.tar.gz
```

镜像基底 Python 3.12 / Caddy 2.10 使用固定次版本系列以接受安全更新；Python 直接依赖固定版本，传递依赖记录在镜像 `/opt/runtime-installed.txt`。**这不是完整字节级可复现锁**。验收后可固定实际镜像 digest、保存镜像，更新安全补丁后重新测试。

`deploy.sh` 构建前把存在的 `tingjian:local` 另标为 `tingjian:previous`，方便**紧邻一次更新**恢复；它不是多版本备份系统，连续多次重试可能覆盖 previous。需要可靠回滚时务必使用独立保存的已验收镜像与匹配源代码/.env：

```bash
# 恢复匹配的上一版本代码和 .env 后执行；不要再次 build 覆盖旧镜像。
sudo docker compose stop caddy app
sudo docker tag tingjian:previous tingjian:local
sudo docker compose up -d --no-build --force-recreate --wait app caddy
sudo bash scripts/doctor.sh
```

只有上一步 previous 确实是已验收版本时才使用。否则从受控备份 `docker load` 并把正确 image ID 标为 local。模型目录版本固定，切模式后旧模型默认保留，方便回退；磁盘清理应由管理员确认哪些版本不再用。

## 访问码轮换

```bash
python3 scripts/configure.py --rotate-code
sudo bash scripts/deploy.sh
```

同时轮换签名密钥，使旧 Cookie 全部无效；不是只修改哈希。普通“退出登录”只清浏览器 Cookie，不能撤销已被复制的签名 Cookie。访问码泄露应立即轮换并审查云端访问/资源状况。

## 常见故障

| 表现 | 先检查 |
|---|---|
| GitHub/PyPI/Docker 拉取失败 | 出站网络、DNS、代理配置；官方文件离线缓存；不要随意替换来源 |
| 模型缺失、wrong argument、missing tokenizer | 是否是固定目录/完整归档、1.12.40 wheel；查看 `engine.py` 与官方 tagged API |
| 137 / OOMKilled=true | 内存上限与物理内存；先单 CTC/关 TTS/单路；自检时停止已有 app |
| 原生推理超时后连接断开 | 应用进程主动退出恢复，不代表该句已成功；查看模型兼容性/CPU/录音长度 |
| 网站打不开或证书错误 | A/AAAA、安全组、80/443 冲突、Caddy 数据卷、系统时钟、CA 限速；禁止用忽略证书验证掩盖问题 |
| 页面能开但不能收音 | 是否可信 HTTPS、用户权限、系统浏览器、麦克风被占用、系统录音权限 |
| WS 403 | Cookie、同源地址、Origin；使用反代后的完整站点，不从另一域名嵌入 iframe |
| WS/接口 429 | 名额/限速；同一公网 IP 默认最多一路；等上一任务结束；不要直接取消配额 |
| 说话过程中没有滚动预览 | AED-only/Nano 只在停顿后出字；dual 繁忙时会跳过临时预览，但仍尝试定稿 |
| 上传较大文件失败 | 默认 50MB/10分钟、客户端上行速度、代理180秒 body超时、录音格式；拆分文件 |
| TTS 不自动播 | 浏览器用户手势限制；点显示出来的原生播放控件；不是自动改土话语义 |
| 老人停顿后句子太碎 | 用同录音测试 VAD_SILENCE，例如从0.7调0.9/1.1；延迟也会增加，不能保证单向改善 |

`doctor.sh` 不打印 `.env`。应用/Caddy 日志仍可能包含域名、IP 或路径；分享前审查。没有真实山东录音，就不要用“看起来能出字”结束项目验收。
