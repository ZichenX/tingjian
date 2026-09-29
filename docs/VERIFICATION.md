# 交付验证记录

记录日期：2026-09-29。版本：1.0.0-rc1。本文记录本次真正执行的验证，不把待执行的验收计划写成已通过。

## 已执行

| 项目 | 结果 | 验证范围 |
|---|---|---|
| Python 测试 | 72 passed | 配置、凭据、音频、队列、下载/解压安全逻辑、HTTP/WS 接口等 |
| Node 测试 | 6 passed | 文字处理、分句，以及实际 AudioWorklet 源码的 PCM/尾帧逻辑 |
| Chromium 离线 DOM/布局 | 7 组检查通过 | 登录状态、文字顺序、临时稿、转义、清空确认、朗读窗口、窄屏布局 |
| Shell 语法 | 所有 scripts/*.sh 通过 bash -n | 仅语法，不等于 Docker/部署命令已经执行 |
| Python/JavaScript 语法 | compileall / node --check 通过 | 仅解析和编译检查 |

Python 测试使用真实 FastAPI/Starlette TestClient 处理 HTTP 和 WebSocket，真实 FFmpeg 解码、真实 soxr 重采样；包括 8/16/44.1/48/96 kHz 输入相关检查。**ASR、VAD 和 TTS 模型在接口测试中使用显式测试桩**，因此这些测试不代表真实模型可运行或山东话准确率。测试桩只在 tests/，不复制进生产镜像。

Node 测试执行仓库的 PCM AudioWorklet 源码，但不获取实体麦克风，因此证明的是转换、分帧、冲刷尾帧等逻辑，不是浏览器权限、驱动、系统音频或移动端后台行为。

离线 Chromium 使用本地 HTML/CSS/JS 和内存 API fixture，没有连接真实后端。在 320、360、390、768 像素宽度下检查最大字号与页面横向溢出；最大字幕字号分别为移动端 53px、较宽视口 60px。桌面截图为 1280 像素视口。没有未捕获 JavaScript 异常。截图内文字专为界面展示填写，**不是 ASR 输出**。

证据：

- [Python 原始运行输出](evidence/pytest.txt)
- [Node 原始运行输出](evidence/node-tests.txt)
- [语法检查输出](evidence/syntax.txt)
- [离线 UI 检查 JSON](evidence/offline-ui.json)
- [桌面截图](images/desktop.png)、[手机宽度截图](images/mobile.png)、[朗读窗口截图](images/mandarin.png)

## 环境与未验证事项

本次运行环境是 Linux x86_64、Python 3.13，而生产 Docker 目标是 Python 3.12。Node 为 22.16.0。测试环境的真实依赖版本另见 [environment.json](evidence/environment.json)。不是在每一种支持系统或架构上都验证过。

本次环境无法联网下载模型，未安装 sherpa-onnx，也没有 Docker。因此：

**没有执行：真实 FireRed/Fun-ASR-Nano/SenseVoice/Melo 模型推理、模型压缩包实际下载、Docker build/Compose/Caddy、公网 DNS/TLS、实际 TTS 音质、山东话 CER、并发吞吐和实体手机测试。** 下载和安全解压的单元测试不等于所有上游归档都已下载成功。

完整浏览器 smoke 曾尝试访问本地测试服务，但当前 Chromium 管理策略返回 `ERR_BLOCKED_BY_ADMINISTRATOR`，并限制麦克风访问。此项明确为**未通过验证/环境阻止**，而不是计入已通过的离线 DOM 检查。不修改或绕过浏览器管理策略。

仓库提供 `.github/workflows/ci.yml`，面向正常 Ubuntu/Python 3.12 环境运行测试、完整测试浏览器和 Docker 测试阶段；该 GitHub workflow 本次没有在 GitHub 上实际执行。完整 browser smoke 仍使用测试 ASR/TTS，不能替代真实模型自检。

模型 API、版本、目录与参数已经对照官方文档和固定 v1.12.40 源码编写，来源见 [SOURCES.md](SOURCES.md)。静态核对不能替代实际权重兼容性验证。

## 目标服务器必须完成的放行步骤

1. `scripts/deploy.sh` 下载/校验权重后，先在独立容器运行真实 `scripts/self_test.py`。它必须真正执行 VAD、所选 ASR、启用时的预览模型和 TTS；不支持 fake 回退。失败则非零退出，不启动一个被假定可用的服务。
2. 模型自检通过后启动服务，检查健康与可信公网 HTTPS。Caddy/DNS/证书失败不能通过 `curl -k` 掩盖。
3. 按 [ACCEPTANCE.md](ACCEPTANCE.md) 用手机移动网络完成人工验收，尤其是停止时的末尾一句、拒绝麦克风、弱网断线和普通话播放。
4. 用目标老人真实山东录音与人工参考文本评测；样例非空不等于准确率达标。不发布未经测量的 CER/延迟/并发数字。

真实自检将在 `models/acceptance/` 写报告和 TTS WAV；源码包不包含虚构的真实模型 PASS 报告。这是部署时的放行机制，不是对任意服务器“完美运行”的承诺。

## 复现命令

在已安装依赖和 FFmpeg 的仓库目录：

```bash
python -m pytest -q --disable-warnings
node --test tests/text.test.cjs
python tests/render_ui.py
for file in scripts/*.sh; do bash -n "$file"; done
python -m compileall -q app scripts tests
node --check web/app.js
node --check web/text.js
node --check web/pcm-worklet.js
```

正常、非受限测试浏览器环境再执行 `python tests/browser_smoke.py`。真实服务器执行 README 部署命令。复现后应保存自己的环境、报告与未通过项，不删除本次交付记录来伪装已完成额外测试。
