# 架构、接口与性能边界

## 运行结构

```text
手机/平板/电脑浏览器（无模型、无 CDN）
    │ HTTPS / WSS；访问码会话 Cookie
    ▼
Caddy :443（TLS、反代；:80 用于证书/重定向）
    │ 内部 Docker 网络，不公开 :8000
    ▼
FastAPI + Uvicorn，1 个进程
    ├── 每连接 soxr 流式重采样 → 16kHz
    ├── 每连接独立 Silero VAD 与有界预览缓冲
    ├── 有界优先级队列 → 1 个 ASR/TTS native 执行线程
    │     final 0 → upload 1 → tts 2 → preview 3
    ├── FireRed CTC/AED，或 SenseVoice / Fun-ASR-Nano
    └── MeloTTS ONNX → WAV
```

集群无入站端口时的替代路径是 `Cloudflare edge → Named Tunnel → cloudflared → app 127.0.0.1:8000`。此路径不运行 Caddy、不开放 80/443；`cloudflared` 必须与 app 在同一节点和同一 Slurm 作业，固定 hostname 通过 `APP_ORIGIN` 参与 Host/Origin/CSRF 校验。具体启动方式见 [Cloudflare Tunnel 部署说明](CLOUDFLARE_TUNNEL.md)。

ASR/TTS 权重只在启动时加载；连接不各自复制 ASR。每个 live 连接有独立 VAD 状态。上传解码使用受限 FFmpeg 子进程，VAD 分段在后台线程进行，ASR/TTS 进入统一 native 工作队列。

“优先级”只调整尚未开始的任务，**不能抢占已经执行中的 TTS/ASR**。取消请求也不会假装取消一个已开始的 C++ 调用。原生调用超过配置超时，进程退出，Docker 按重启策略恢复；所有连接会断开，界面保留已经收到的 final 并提示重新开始。

## 实时协议

同源 `wss://站点/api/live`，需要 Cookie 和正确 Origin。不要把访问码或 Cookie 放 URL。

首条客户端 JSON：

```json
{"type":"start","csrf":"由 GET /api/session 返回","sample_rate":48000}
```

服务端准备好后：

```json
{"type":"ready","partial":true,"max_seconds":1800}
```

之后客户端发送单声道 **PCM16 little-endian 二进制帧**。采样率是实际 AudioContext rate，可为 44.1/48kHz 等，不是口头声明的 16kHz。前端每 2048 样本一帧；后端单帧上限半秒、每秒帧数/累计超实时发送限制，不能加速上传文件到 live 接口。

服务端逐条 JSON：

```json
{"type":"partial","id":0,"text":"仍可能修改的文字"}
{"type":"final","id":0,"text":"本段处理完成的文字","start":0.2,"end":4.8}
{"type":"notice","message":"提示"}
{"type":"error","message":"本段/会话失败说明"}
{"type":"done","ok":true}
```

`id` 在当前会话中递增；UI 额外使用本地 run 编号区分多次录音。`start/end` 是 VAD 片段秒数，不是逐字时间戳。final 不表示字词客观正确，只表示本段推理完成。

停止时前端先给 AudioWorklet 发 `flush`，将尾部不足2048样本的 PCM 也发送；收到 flushed 或短超时后才发 `{"type":"stop"}`。服务端 flush soxr，再补足 VAD 最后窗口并 flush，等待 pending finals，发送 done。**最后一句不依赖额外静音包才能完成。** 超时或未收到结束确认会提示末尾可能不完整。

等待首次 hello 10秒；连接没有音频40秒超时；单次会话默认30分钟；最大段12秒；缓冲区有界。浏览器切后台主动停止收音，不保证锁屏后台字幕。断线不自动重复上传，避免重复字幕与隐私误收音；由用户手动重新开始。

## HTTP 接口

| 方法/路径 | 鉴权与输入 | 输出 |
|---|---|---|
| GET `/`、`/assets/*` | 公共静态界面 | HTML/CSS/JS |
| GET `/health/live` | 无需登录 | `{"ok":true}`，只证明进程可响应 |
| GET `/health/ready` | 无需登录 | 200/503，启动时真实模型是否加载 |
| POST `/api/login` | 同源 Origin；JSON `{code}` | HttpOnly Cookie；访问码从不回显 |
| GET `/api/session` | Cookie | CSRF、模式能力、上传/会话限制 |
| POST `/api/logout` | Cookie + Origin + X-CSRF-Token | 清本浏览器 Cookie |
| POST `/api/transcribe` | Cookie + Origin + CSRF；**raw file body** | `application/x-ndjson` 流 |
| POST `/api/tts` | Cookie + Origin + CSRF；JSON `{text,speed}` | `audio/wav` |

上传不是 multipart，也不接受任意 URL；不需要文件名参与服务端文件路径。最多一份上传处理中。服务器先接收并解码，开始识别后逐段发送 `progress/final/done`，失败发送 error。NDJSON 会保留已产出部分，不把部分成功冒充完整完成。上传/处理期间取消会尽力释放请求资源，已经进入 native 的任务仍需执行完或超时退出。

TTS 每请求1～120字符，语速0.7～1.2。前端编辑器最多1500字，按标点或60字符切块；最多预取下一块，不一次排队全部长文。本设备不与实时收音并行。TTS 输出在内存中生成，不建立永久音频下载目录。

错误：400 输入/音频无效；401 登录失效；403 同源/CSRF/Host 不符；408 上传或解码超时；413 超出限制；415 JSON Content-Type 不符；429 容量/频率限制；503 功能/推理不可用。WS 错误会尽量先传中文说明再关闭连接。

## 延迟与准确率如何解释

VAD停顿等待 + 排队 + 推理 + 网络 + 页面更新，才是用户看到定稿的总延迟。单条命令的 RTF 不等于这个延迟。模拟实时频繁重算前缀会消耗额外 CPU，所以不能把离线处理倍速直接当作并发路数。

默认 CTC 临时预览、AED 定稿是工程分工假设，不是已测的山东话精度排名。`aed-only` 与 `dual` 的离线最终模型相同，不能拿它们的同音频最终稿差异当作“流式损失”。相同模型/音频/分段/参数原则上可得到相近最终结果；实际差别需分别测 VAD 边界、采集音质、噪声、上下文和实时调度。

本版不集成额外标点模型、说话人分离、情绪判断、跨片段全局语言模型、自动方言语义改写或训练/微调。它们会增加复杂度，且不是长者首版界面的必要条件。模型自带标点/ITN 保留；没有标点时按 VAD 段分行。
