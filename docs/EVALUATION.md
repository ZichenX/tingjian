# 用真实山东话选择模型，而不是猜准确率

## 建立测试集

先准备例如30～60分钟、15～30名经授权说话人的真实录音，作为首轮工程试验；这只是建议规模，不是统计上保证代表全山东的样本量。覆盖实际服务地区、不同年龄/性别、轻口音/重方言、近讲/远讲、安静/噪声。不要为了凑“山东”而忽略胶东、鲁西南等实际差异；按目标使用人群分组。

人工逐字标注，地方词尽量保留，不先改写成普通话。先约定同音写法、语气词、重复、数字、简繁和标点规则。不要让另一个ASR模型充当“正确答案”。标注人员不了解的重方言应复核；重要人名/地名另设实体正确率检查。

目录示例（语音需要你自己提供，仓库不附虚假的山东录音）：

```text
evaluation/
  manifest.jsonl
  audio/001.wav
  audio/002.wav
```

`manifest.jsonl` 每行一个JSON，示例只展示格式，文字必须与你的真实音频相符：

```jsonl
{"audio":"audio/001.wav","text":"人工逐字标注第一条录音","group":"济南_近讲_长者"}
{"audio":"audio/002.wav","text":"人工逐字标注第二条录音","group":"胶东_远讲_长者"}
```

## 下载用于对照的模型

先阅读所有替代模型许可，尤其SenseVoice独立模型条款。随后在已经构建的项目根目录：

```bash
sudo docker compose run --rm --no-deps tools python scripts/download_models.py --all
```

这只下载，不把全部模型同时加载到服务中。磁盘需容纳原始压缩包、解压模型和临时解压空间。

## 分进程运行离线评测

建议维护窗口暂停服务，避免测试与线上抢CPU/内存。把 `evaluation/` 放仓库根目录，自行保护其中的录音；报告含参考/识别文字，不公开分享敏感内容。

```bash
sudo docker compose stop caddy app
mkdir -p reports/evaluation

for engine in ctc-only aed-only funasr-nano sensevoice; do
  sudo docker compose run --rm --no-deps \
    -v "$PWD/evaluation:/evaluation:ro" \
    -v "$PWD/reports/evaluation:/reports" \
    tools python scripts/evaluate.py \
      --engine "$engine" \
      --manifest /evaluation/manifest.jsonl \
      --output "/reports/$engine.json"
done

# 评测结束恢复当前 .env 指定模式；不改变当前配置。
sudo docker compose up -d --wait app caddy
```

脚本按单进程单引擎顺序运行，自动关闭TTS，输出逐条/分组/总体结果。输入先由FFmpeg统一单声道16kHz，之后相同VAD参数识别。某条失败会退出，不悄悄跳过差样本。

默认归一化：NFKC、小写、删除空白和Unicode标点。不做简繁转换、不把“一百”与“100”视为同一答案、不替换方言词。需要严格原字比较用 `--no-normalize`；也可在评测前制定并固定统一标注规则。

## 看哪些指标

**CER = 总编辑距离 / 总参考字符数**，不是各句CER的简单平均；字符插入过多时可超过100%。不要把1-CER包装成通用“识别准确率”。特别关注地区、长者与噪声组，而不是只看总体。

**pipeline RTF** = VAD+ASR处理时间 / 录音时长，脚本明确不包含模型加载、解码、网络、队列和UI。不代表手机说话到出字延迟，不代表两路并发保证。自检/评测中生成的时间才是你的服务器实测数据。

实体错误另行检查：人名、村镇名、金额、日期、药名等。对每条重要实体标注正确/错误并统计，不能被普通高频字的好成绩掩盖。TTS也需抽查多音字、姓名和数字读法。

## 实时与离线的对照不能混淆

默认dual最终使用AED，因此同录音、同VAD/参数的dual离线最终稿与aed-only是相同模型路径。对照CTC/AED是在比较解码模型；不是自动在比较“流式/离线”。

实时损失应另测：固定原始录音，以原速送入实时路径，记录每条最终稿与到达时间，再与同录音离线转写比较。预览字错率和最终字错率分开报告。人工测量至少看首字延迟、停顿至定稿时间、中位数/高分位、重复/漏句、停止尾句、断线行为。普通浏览器麦克风录音与上传高质量文件不等输入时，差异不能全部归因于streaming。

调整VAD_SILENCE会改变分句与等待；调大并不保证所有方言更准。以真实长者的自然停顿测试，避免连续过度切碎；保留最大段长限制，不能把一小时音频直接给离线模型。

决定上线前，由用户定义可接受的方言关键字/实体错误率和字幕延迟，而不是套用没有依据的统一门槛。先少量家人/试用者使用，确认真实交互，再扩大范围。
