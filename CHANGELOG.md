# 变更记录

项目遵循语义化版本的近似约定；模型权重、上游依赖和部署脚本变化可能需要重新执行真实模型验收。

## 1.0.0-rc1

首个可复现候选版本：

- FastAPI + Uvicorn 单进程服务和大字网页界面；
- FireRedASR2 CTC/AED 双阶段路径，以及 Fun-ASR-Nano、SenseVoice 对照引擎；
- Silero VAD、soxr 流式重采样、上传转写和 MeloTTS ONNX 普通话朗读；
- 访问码、签名 HttpOnly Cookie、CSRF/Origin/Host 校验、频率限制和并发准入；
- 有界优先级推理队列、上传/音频时长/请求体限制和原生调用超时；
- Docker Compose + Caddy HTTPS 部署；
- 固定 Cloudflare Named Tunnel 的集群原生部署路径；
- 模型 HTTPS 下载、断点续传、安全解压、SHA256 内容锁和真实 self_test.py；
- Python、Node、离线 UI、浏览器 smoke 与 Docker test 阶段；
- 中文部署、架构、评测、安全、许可和验收文档。

真实模型权重、访问码、.env、用户录音和运行时验收产物不随源码发布。
