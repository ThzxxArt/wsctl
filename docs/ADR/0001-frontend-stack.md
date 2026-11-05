# ADR-0001: Vue 3 + TypeScript + Vite 前端技术栈

- 状态：已接受
- 日期：2026-11-04
- 决策者：wsctl maintainers
- 相关：ROADMAP-0.2.0 §4.2、§5

## 背景

0.1.30 的前端是 5887 行 IIFE（`src/wsctl/static/app.js`），所有状态是模块级变量，
DOM 靠 `innerHTML` 与手工 `createElement` 拼装，59 条测试通过正则匹配源码字符串来
保护设计决策。它证明了「零构建」在早期是正确取舍，但也把每次功能扩展变成了对全局
状态的重新推理。

0.2.0 需要：标签/分屏工作台、命令块、文件编辑、多分享链接、管理面板、主题导入、
PWA、移动端工具栏，以及可单测的协议客户端。

## 决策

采用 **Vue 3.5 + TypeScript 5.9 + Vite 8 + Pinia 4 + Naive UI 2.45 + Tailwind 4**
重写前端，xterm.js 升级到 6，布局继续使用二叉树 + splitpanes 4。

构建产物由 Vite 输出到 `src/wsctl/static`，wheel 内置预构建文件；最终用户仍然
`pip install` 即用，不需要 Node。

## 备选方案

| 方案 | 结论 | 原因 |
|---|---|---|
| 继续原生 JS | 拒绝 | 状态与 DOM 手工同步的边际成本已经超过重写成本 |
| React 19 | 拒绝 | 不是能力问题；Vue SFC 更贴近现有模板结构，迁移阅读成本更低 |
| Svelte 5 | 拒绝 | 生态与团队熟悉度不如 Vue；终端类组件库支持少 |
| Lit / Web Components | 拒绝 | 缺少成熟的状态、路由、表单与测试组合 |
| dockview / golden-layout | 拒绝 | 会接管 xterm 的 DOM 生命周期，resize/focus 难以精确控制 |
| 保留零构建 | 拒绝 | 开发者体验与类型安全不可接受；用户侧零构建通过预构建保留 |

## 后果

**正面**

- 组件边界、TypeScript 类型、Pinia 状态与 Vitest 单测；
- Vite 代码分割：编辑器、回放、管理面板按需加载；
- 预构建产物可做 bundle budget 与 SRI/SBOM；
- xterm 6 的 WebGL/Canvas 回退、Unicode 11、Image/Serialize addon。

**负面 / 风险**

- 仓库新增 `web/`、`pnpm-lock.yaml` 与 Node 供应链；
- 发布前必须构建前端，CI 增加 frontend/browser job；
- 旧 `tests/test_ui_contracts.py` 的源码字符串契约全部失效——这是有意的，
  替换为 Vitest + Playwright + 少量构建契约。

## 实施约束

- 用户侧不得要求 Node；`web/` 只存在于源码与 sdist。
- 前端源码不出现 `.js` 单体；单文件超过 1200 行视为回归。
- 初始 JS（gzip，不含懒加载 chunk）必须 < 500 KiB。
- 所有交互必须可通过键盘到达，`axe-core` 0 critical。
