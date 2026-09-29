# 贡献指南

感谢你改进「听见」。这个项目面向真实的听障沟通场景，优先保证结果语义、隐私和明确失败，再考虑性能或功能扩张。

## 开发环境

源码级测试需要：

- Python 3.12（CI 使用 Python 3.12）
- Node.js 22（前端文本测试）
- Linux 上的 FFmpeg、libgomp1 和可用字体
- Playwright Chromium（浏览器 smoke 测试）

创建虚拟环境并安装开发依赖：

~~~bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
~~~

不要把生产 .env 复制到 issue、测试 fixture 或提交中。需要本地运行时请使用：

~~~bash
python3 scripts/configure.py --dev
python3 scripts/local.py prepare
python3 scripts/local.py run
~~~

--dev 只绑定 localhost，不是公网部署方式。测试桩只允许放在 tests/，不能从 app/ 或生产 Docker 镜像导入。

## 提交前检查

~~~bash
python -m pytest -q
node --test tests/text.test.cjs
python tests/render_ui.py
python -m compileall -q app scripts tests
node --check web/app.js
node --check web/text.js
node --check web/pcm-worklet.js
for file in scripts/*.sh; do bash -n "$file"; done
~~~

有可用的非受限浏览器环境时，再运行：

~~~bash
python -m playwright install --with-deps chromium
python tests/browser_smoke.py
~~~

CI 会构建 Dockerfile 的 test 阶段。真实模型推理不放入普通 CI：模型权重体积大、许可独立，必须在目标主机执行 scripts/download_models.py 和 scripts/self_test.py。

## 代码约束

- 保持 Uvicorn 单 worker；不要为了扩容直接增加多 worker。模型、限流和容量状态是单进程设计。
- 不把应用绑定到 0.0.0.0 作为公网解决方案；正式入口必须是 Docker 内网+Caddy 或固定 Cloudflare Named Tunnel。
- 不记录 Cookie、访问码、完整音频或完整用户文字；不要新增默认持久化录音/转写库。
- 用户识别结果进入网页时使用 textContent，不拼接 HTML。
- 任何上传、解压、模型下载、外部代理头都必须有边界检查和失败路径。
- partial 是可能变化的预览，不能当作最终文字保存、朗读或计入完成结果。
- 真实模型、上游版本和许可证变化要同步更新 MODEL_LICENSES.md、docs/SOURCES.md 和相关验收说明。
- 用户录音、截图中的个人信息、访问码、Tunnel token 和 .env 永远不要提交。

## Pull request 内容

请在 PR 描述中写清楚：

1. 改了什么以及为什么；
2. 影响的入口、模型、隐私或容量边界；
3. 运行了哪些检查，哪些检查因环境未运行；
4. 是否需要迁移 .env、模型目录或部署文档；
5. 如果影响 UI，请附无个人信息的桌面/窄屏截图。

涉及认证、上传、代理头、模型下载器、容器权限或公网入口的改动，需要补充对应测试或说明为什么无法自动测试。不要把“本地 fake 测试通过”描述成真实模型准确率或公网验收通过。
