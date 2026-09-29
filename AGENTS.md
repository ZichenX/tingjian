# Coding agent 接手说明（先读）

## 目标与不可破坏的约束

为听障长者提供公网大字中文转写与可选普通话朗读。首版宁可简单、明确失败，也不静默丢字、不假装识别成功、不以模拟字幕代替真实推理。

1. **生产入口**：专用 Docker 主机使用 `sudo bash scripts/deploy.sh`；无入站端口的集群可使用固定 hostname 的 Cloudflare Named Tunnel 路径（`scripts/run_public_native.py` + `scripts/run_cloudflare_tunnel.sh`）。两条路径都必须经过 `download_models.py` 与 `self_test.py`，后者真正执行 native 模型。`tests/fakes.py` 仅测试，绝不引入 `app/` 或生产镜像。
2. **CPU / 单进程**：Python 3.12，sherpa-onnx 1.12.40，Uvicorn 单 worker。ASR/TTS 通过一个专用执行线程；live final 优先，预览可跳过。不要用多 worker 扩容，否则锁、配额失效并复制全部模型。
3. **Caddy 路径公开端口只有 Caddy 的 80/443**。应用的 8000 不能 publish。集群 Tunnel 路径不开放入站 80/443，app 和 cloudflared 必须在同一节点，app 只绑定 loopback。Caddy 覆盖 X-Real-IP；Tunnel 仅在 loopback 对端时使用 Cloudflare 的 CF-Connecting-IP；不要信任外来请求自行提交的代理头。公网使用固定 hostname、可信 HTTPS 与正确 DNS。
4. **客户端不带长期密钥**。访问码只在首次表单 POST；服务端存 PBKDF2 哈希；会话为 HttpOnly/Secure/SameSite Cookie；写操作有 Origin+CSRF；WS 第一个 JSON 校验 CSRF。禁止把访问码放 URL、localStorage 或日志。
5. **隐私**：不增加分析跟踪、默认转写数据库、自动保存原始录音或外部推理 API。日志不记完整用户文字/音频。新增数据保留必须有明确告知与删除机制。
6. **结果语义**：`partial` 未定稿、不保存、不朗读；`final` 才进入已确认文字。final 的“确认”只是完成模型处理，不代表事实正确。断线保留已有 final 并提示最后一句可能不全。
7. **模拟流式，不是真流式模型**。先收集音频并 VAD 切段，再重复部分离线推理；不承诺固定毫秒延迟。Fun-ASR-Nano/AED-only 不重复预览。
8. **语音互斥**：本设备开始 TTS 前必须停止麦克风；播放不可自动开始。TTS 读文字，不是方言语义翻译；不要默认接 LLM 擅自改动姓名、数字或含义。

## 首次执行顺序

读 `README.md → MODEL_LICENSES.md → docs/DEPLOYMENT.md → docs/VERIFICATION.md`。询问/取得用户掌控的服务器 SSH、公网域名、证书邮箱及 DNS/安全组信息；不能凭空生成可用域名，也不能在没有授权时改其他服务。

先检查系统、架构、内存磁盘、80/443 占用。安装基础环境；交互方式生成访问码或使用权限 600 的临时 `--code-file`（勿放命令参数、仓库或日志）。确认模型条款后运行部署脚本。模型网络受限按离线缓存文档处理，不随意猜测第三方镜像链接。

## 明确的验收定义

- 单元/接口测试与 Node 测试全部通过；测试结果不能冒充模型准确率。
- 默认/每个实际启用的替代引擎，真实 `self_test.py` 退出码为 0；报告包含实际非空 ASR 和 TTS WAV（TTS 开启时），而不仅检查 `.onnx` 文件存在。
- `docker compose up ... --wait` 成功，公网 HTTPS `/health/ready` 为 200/ready=true；这仍不足以证明手机可访问。
- 用移动网络在真实手机测试首次登录、麦克风权限拒绝/允许、说话/停止末尾句、录音上传、TTS、断线、换后台、文字大小与保存。
- 使用真实山东录音与人工参考文本评测，按地区/年龄/环境分组；未有数据不得写“山东话准确率 XX%”。
- 输出交付记录：服务器配置、镜像 ID、依赖清单、模型 lock、自检报告、人工验收结果、已知限制；隐藏 .env 与个人录音。

## 易错接口与固定布局

详见 `app/engine.py`。本版按 **v1.12.40** API 编写，不用印象中的其他框架接口：

- FireRed CTC：`OfflineRecognizer.from_fire_red_asr_ctc(model=..., tokens=...)`。
- FireRed AED：`from_fire_red_asr(encoder=..., decoder=..., tokens=...)`。
- Fun-ASR-Nano：`encoder_adaptor.int8.onnx + llm.int8.onnx + embedding.int8.onnx + Qwen3-0.6B/`，不是另一套 prefill/decode 双文件布局。
- VAD：`vad.front` 是属性，不是 `front()`。以 VAD 的 completed samples 为定稿依据；预览缓冲不能替代最终音频。
- TTS：`OfflineTtsVitsModelConfig` 带字典、tokens、lexicon；`GenerationConfig(sid/speed)` 通过属性设置；`tts.generate(text, generation_config)`。不要引入需联网安装语言包的另一个 Melo 原版 pipeline。
- 麦克风送 **真实 AudioContext 采样率** + PCM16LE，后端 soxr 流式重采样；不应声称浏览器请求 16kHz 就实际输出 16kHz。
- 上传是 **raw HTTP body**，不是 multipart。客户端 `file` 直接作为 fetch body。
- UI 文本必须 `textContent`，不接受识别内容作为 HTML。

## 更改与排错规则

不“修复”为关闭证书验证、去掉访问码、开放 8000、吞掉异常、无限队列、全模型同时常驻或 `--privileged`。不要为绕过错误直接升级所有依赖；先复现 pinned 版本、查看故障与官方 API。缺包/缺模型须明确失败。

当前包的本地测试环境与生产目标不同，且没有完成真实权重、Docker、公网/实体手机验收。不要删除这条边界直到在目标环境取得可重复证据。浏览器完整 smoke 在交付环境被浏览器策略阻止；提供的离线 DOM 检查不等于端到端麦克风验收。

## 推荐进一步完善的优先级

先保证目标服务器真实推理和手机尾句正确，再用录音选模型/VAD 参数，然后优化延迟。后续如面向大量用户，需设计独立账户、持久配额、队列/worker 服务、审计与横向扩容，不能靠放大现有常量直接宣称生产规模能力。
