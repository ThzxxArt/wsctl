# 前端开发指南（wsctl 0.2.0）

## 目录

```text
web/
├── src/
│   ├── api/            REST 客户端与类型
│   ├── components/     Vue 组件（Workspace/Terminal/File/Share/...）
│   ├── protocol/       协议 v2 帧、WebSocket 客户端、IndexedDB 缓存
│   ├── stores/         Pinia（auth/sessions/workspace/prefs/terminal/ui/presence）
│   ├── utils/          快捷键、主题导入
│   ├── test/           Vitest setup
│   ├── i18n.ts         zh-CN / en
│   ├── styles.css      Tailwind 4 + 设计令牌
│   └── main.ts
├── tests/e2e/          Playwright（Chromium/Firefox/WebKit/移动端）
├── public/             favicon、manifest、service worker
├── vite.config.ts
└── playwright.config.ts
```

## 命令

```bash
cd web
pnpm install
pnpm dev          # 代理到 127.0.0.1:7681
pnpm typecheck
pnpm lint
pnpm format:check
pnpm test         # Vitest + Vue Test Utils
pnpm api:generate # FastAPI OpenAPI -> src/api/schema.d.ts
pnpm build        # 输出到 ../src/wsctl/static
pnpm e2e          # 需要先 build；Playwright 会启动 7801 的测试服务
```

`pnpm e2e` 默认使用 `python -m wsctl`；本地如果包装在别的解释器里：

```bash
WSCTL_PYTHON=../.venv313/bin/python pnpm e2e
```

## 发布约束

- 构建产物提交到 `src/wsctl/static`，wheel 直接包含；
- `src/wsctl/static/app.js` 与 `vendor/` 已删除，不再手工维护；
- 资源文件名带内容哈希，服务端对 `/static/assets/*` 发送
  `Cache-Control: public, max-age=31536000, immutable`；
- 构建后为 `index.html` 中的入口资源注入 `integrity="sha384-..."`（SRI）；
- `index.html`、`sw.js`、manifest 必须 `no-cache`；
- CSP 不含 `script-src 'unsafe-inline'`，因此不能在 `index.html` 写内联脚本；
- 首屏 JS（gzip，不含懒加载 chunk）预算 500 KiB；CI 会检查。

## 调试

- `localStorage.setItem("wsctl-debug","1")` 后刷新，可在控制台通过
  `window.__wsctlTerminals` 访问 xterm 实例；Playwright 用它读取屏幕缓冲区。
- 连接状态、gap、快照事件都会出现在状态栏与 Toast；详细日志在浏览器控制台。
- 协议帧的编解码单测在 `src/protocol/frames.spec.ts`，改帧格式先改测试。

## 测试哲学

0.1.x 用正则解析 `app.js` 源码来保护设计。0.2.0 不这样做：

- 行为用 Vitest（协议、store、工具函数、Vue 组件）；
- 真实浏览器用 Playwright（终端输入、分屏、面板、断网恢复、a11y、视觉基线）；
- 打包正确性用 `tests/test_frontend_build.py`（含 SRI 校验）；
- 颜色对比度用 `tests/test_contrast.py` 读 `styles.css`；
- API 路径由 `openapi-typescript` 生成，CI 重新生成并 `git diff --exit-code`。

任何「必须存在某段字符串」的测试都属于上一代，不要加回来。
