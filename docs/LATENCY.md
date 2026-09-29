# 延迟分析与优化

## 当前流水线

实时听写路径是：

~~~text
浏览器 AudioWorklet
  → WebSocket 二进制 PCM16
  → 服务端 soxr 重采样到 16 kHz
  → Silero VAD 分段
  → CTC 临时预览（dual）
  → AED 最终识别
  → WebSocket 返回 partial/final
~~~

这是“模拟流式”路径：ASR 模型本身按一段音频离线解码，服务端通过短段、VAD 和前缀重算提供临时字幕。不能把网络帧到达时间当作模型已经完成识别的时间。

## 已确认的延迟来源

当前默认配置：

~~~text
ASR_MODE=dual
ASR_THREADS=3
VAD_SILENCE=0.7
SEGMENT_SECONDS=12
PARTIAL_INTERVAL=1.8
~~~

代码层面的固定成本：

- VAD 需要等待 VAD_SILENCE 才确认一句话结束；
- 浏览器 AudioWorklet 每 2048 个输入采样发送一帧，48 kHz 时约 42.7 ms；
- 前端停止收音时等待 flush，最长 800 ms，但正常情况下通常由下一次音频处理周期完成；
- 所有最终 ASR、上传转写、TTS 共享一个 InferenceWorker；
- 旧实现中，CTC 临时预览也会占用这个最终任务 worker；
- 单次 native 推理已经开始后不能被 Python 安全取消。

真实模型验收样例中，10.05 秒音频的 ASR 总耗时为 5.761 秒，其中一段 6.26 秒语音的单次推理耗时为 4.256 秒。这是单样例基线，不是并发承诺，也不代表山东话 CER。

## 本次低风险改动

### 1. dual 模式分离预览队列

CTC 与 AED 是两个不同的 recognizer。当前版本在 dual 模式下给 CTC 预览单独建立一个小的 best-effort worker：

- AED final 仍使用原来的最终 worker；
- CTC preview 不再阻塞 AED 队列中尚未开始的最终任务；
- 预览仍可被丢弃或过期，最终文字不会被丢弃；
- ctc-only 和 sensevoice 仍使用单 worker，因为它们的 preview/final 复用同一个 native recognizer；
- 模型文件、输入音频、VAD 参数和 AED 最终解码设置不变。

这项改动主要改善“刚好有预览推理运行时，用户停止后要多等一轮”的情况。它不保证单用户每句话都减少固定秒数；它会增加少量 CPU 并行度。

### 2. 服务就绪前预热

启动时对每个启用的 ASR recognizer 和 TTS 做一次短的确定性调用。ONNX Runtime/native decoder 的首次 kernel、线程池和内部缓存初始化被移到 /health/ready 变为 ready 之前，避免第一位用户承担冷启动成本。

预热只改变启动耗时，不改变模型权重、音频分段和用户输出。若模型预热失败，应用不会把未完全验证的实例标记为 ready。

## 线程数 A/B 计划

当前作业分配的 CPU 数高于 ASR_THREADS=3，但不能据此直接把线程数改成 CPU 总数。应在独立节点或维护窗口运行相同样例：

~~~bash
set -a
. ./.env
set +a

for t in 3 6 8; do
  ASR_THREADS=$t OMP_NUM_THREADS=$t OPENBLAS_NUM_THREADS=1 \
    python scripts/self_test.py --engine dual \
    --output-dir "reports/latency-$t"
done
~~~

记录：

- asr_seconds、asr_rtf；
- 每个 segment 的 inference_seconds；
- peak_rss_kib_linux；
- 输出文本是否逐字一致；
- 两路实时会话同时运行时的排队等待；
- TTS 是否因 CPU 争用变慢。

只有在真实录音输出一致、RTF 改善且内存稳定时，才把 ASR_THREADS 写入运行环境。建议先比较 3、6、8，不建议直接使用 24 或开启多 Uvicorn worker。

## 参数取舍

| 参数/方案 | 延迟收益 | 对效果的风险 |
| --- | --- | --- |
| ASR 线程 3 → 6/8 | 可能明显降低 native 推理时间 | 需验证线程争用和数值一致性 |
| dual 预览单独 worker | 减少预览阻塞 final | 增加 CPU 并行度 |
| 启动预热 | 降低首次请求冷启动 | 只增加启动时间 |
| 固定 Named Tunnel | 减少入口抖动和额外代理 | 不能替代模型压测 |
| VAD_SILENCE 0.7 → 0.5 | 约少等 0.2 秒 | 可能切掉尾音或改变分段 |
| SEGMENT_SECONDS 调小 | 长句更快出 final | 上下文变短，可能影响方言词 |
| ctc-only | 通常更快 | 最终效果不同 |
| aed-only | final 模型与 dual 相同、去掉 partial | 没有临时预览，交互反馈变化 |
| 增加 Uvicorn worker | 对单次推理帮助很小 | 重复加载模型、破坏单进程容量状态 |

## 公网入口测量

应该把以下三个时间分开：

1. 页面加载/登录到 WebSocket ready；
2. 说话停止到第一个 final；
3. 停止到 done。

当前本机 app 的健康请求是亚毫秒级，而从登录节点新建当前 Quick Tunnel HTTPS 请求需要数秒。这说明连接建立路径需要单独测量；它不能被误认为每一个 ASR segment 都有同样的网络延迟。固定 Named Tunnel、手机 4G/5G 和浏览器保活 WebSocket 应分别验证。

## 不应直接做的事情

在没有真实方言录音 A/B 之前，不要：

- 把 VAD_SILENCE 降到很低；
- 把 SEGMENT_SECONDS 大幅缩短；
- 切换到 ctc-only 并宣称效果不变；
- 直接增加 MAX_LIVE_SESSIONS；
- 启动多个 Uvicorn worker；
- 为了速度删除访问控制、队列和音频长度限制。

延迟优化最终必须同时报告：模型版本、线程数、录音集合、最终文字差异、RTF、峰值内存和公网测量路径。
