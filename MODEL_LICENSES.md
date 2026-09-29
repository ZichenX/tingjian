# 模型、依赖与许可边界

核对日期：2026-09-29。这里记录来源和模型卡中的许可标签，不替代各上游的完整条款。软件许可证和模型权重许可证不是一回事；开源也不等于对训练数据、声音、商业用途作无限保证。

本仓库不附带权重或第三方字体。安装脚本保留模型包自带的 LICENSE、README 等文件，不对其重新授权。部署前须阅读所选模型的完整条款，然后使用 `configure.py --accept-model-licenses` 确认已审阅。首次确认只是一项操作门槛，不能授予上游未授予的权利。切换替代模型或使用 `--all` 前也应重新审阅。

| 组件 | 所用版本/用途 | 核对到的来源与许可信息 |
|---|---|---|
| sherpa-onnx | Python 1.12.40，CPU ASR/TTS/VAD 运行时 | [仓库与 LICENSE](https://github.com/k2-fsa/sherpa-onnx/tree/v1.12.40)，Apache-2.0 |
| FireRedASR2-AED | sherpa 官方 2026-02-26 AED INT8，以及 2026-02-25 CTC 分支 INT8 | [原始模型卡](https://huggingface.co/FireRedTeam/FireRedASR2-AED) 标记 Apache-2.0；[转换模型文档](https://k2-fsa.github.io/sherpa/onnx/FireRedAsr/pretrained.html) |
| Fun-ASR-Nano-2512 | 2025-12-30 INT8 ONNX，替代模型 | [原始模型卡](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512) 标记 Apache-2.0；[转换文档](https://k2-fsa.github.io/sherpa/onnx/funasr-nano/pretrained.html) |
| SenseVoiceSmall | 2024-07-17 INT8，多语言轻量对照 | [原始模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) 标记 **model-license**；应阅读其中的完整模型条款，不能直接以工具包的 Apache 许可替代。不是默认引擎。 |
| MeloTTS-Chinese | `vits-melo-tts-zh_en` 单说话人 ONNX | [原始模型卡](https://huggingface.co/myshell-ai/MeloTTS-Chinese) 标记 MIT；[转换文档](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html#vits-melo-tts-zh-en-chinese-english-1-speaker)；保留归档内 LICENSE |
| Silero VAD | sherpa 发布的 `silero_vad.onnx` | [原始项目与 LICENSE](https://github.com/snakers4/silero-vad)，MIT；[sherpa 用法](https://k2-fsa.github.io/sherpa/onnx/vad/silero-vad.html) |

依赖中的 FFmpeg、soxr、NumPy、FastAPI、Uvicorn、Caddy 等各有独立许可证。Docker 使用发行版提供的 FFmpeg 包；重新分发镜像或商用发行时，需要一并履行依赖的声明、源码提供等适用义务，不能只附本仓库 MIT 文件。

`models/artifacts.lock.json` 保存下载来源、实际 SHA256 与解压后文件校验值。首次默认是 **HTTPS + 首次信任（TOFU）**，不是上游签名验证。没有下载到文件之前，仓库不会伪造“预先确认的 SHA256”。需要更严格的供应链控制时，独立核实上游文件并通过 `download_models.py --checksums ... --require-trusted-hashes` 导入可信哈希。

不要引入来源不明的模型、冒充他人的克隆音色，或把评测录音误用为可公开分发的素材。用户录音也应在得到适当授权后处理。
