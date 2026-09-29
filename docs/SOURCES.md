# 技术来源与核对范围

核对日期：2026-09-29。以下以项目官方文档、源码、模型卡为主。文档随着上游变化可能更新；本项目运行API固定在sherpa-onnx **v1.12.40**，模型固定目录/发布日期，而不是运行时追随latest。

- FireRedASR2 ONNX预训练模型、明确包含山东话的覆盖声明、CTC只导出分支、AED/CTC文件布局：
  https://k2-fsa.github.io/sherpa/onnx/FireRedAsr/pretrained.html
- FireRed原始模型与许可：
  https://huggingface.co/FireRedTeam/FireRedASR2-AED
  https://github.com/FireRedTeam/FireRedASR2S
- Fun-ASR-Nano ONNX布局与示例：
  https://k2-fsa.github.io/sherpa/onnx/funasr-nano/pretrained.html
  https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512
- SenseVoice ONNX基线（本项目用2024-07-17版本，不混入2025粤语微调版本）：
  https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
  https://huggingface.co/FunAudioLLM/SenseVoiceSmall
- MeloTTS ONNX：中文/英语单音色、约163MB、44.1kHz；英语限词典内词，不是任意多语言朗读：
  https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html#vits-melo-tts-zh-en-chinese-english-1-speaker
  https://huggingface.co/myshell-ai/MeloTTS-Chinese
- 固定版Python识别器工厂：
  https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.40/sherpa-onnx/python/sherpa_onnx/offline_recognizer.py
- 固定版VAD/TTS示例和字典配置：
  https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.40/python-api-examples/vad-microphone.py
  https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.40/python-api-examples/offline-tts.py
  https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.40/sherpa-onnx/csrc/offline-tts-vits-model-config.h
- Caddy自动HTTPS与部署前提：
  https://caddyserver.com/docs/automatic-https
  https://caddyserver.com/docs/caddyfile/directives/request_body
- 浏览器麦克风权限/安全上下文：
  https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- Docker官方Ubuntu安装：
  https://docs.docker.com/engine/install/ubuntu/

## 没有从这些来源推导的结论

这些资料不证明本用户的济南/胶东/鲁西南等实际口音CER，不证明INT8 CTC与AED的具体精度差，也不证明本项目默认两路在任何CPU上都实时。未公开或未亲测的数据不补造百分比/毫秒数。

默认双阶段、16GB主机起点、1.8秒预览间隔、0.7秒VAD停顿和12秒段长是**本项目工程选择**，不是原作者的统一最佳配置。生产应通过实际录音与用户体验校准。
