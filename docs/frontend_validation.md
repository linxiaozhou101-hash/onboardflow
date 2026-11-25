# 前端实现与实测记录

本地中文界面使用 React 19.1.0、TypeScript 5.8.3、Vite 6.3.5、lucide-react 0.511.0；精确版本和传递依赖见 `frontend/package-lock.json`。没有外部字体、图片或 CDN 运行依赖。界面从同源 `/api` 读取实际数据库记录。

## 已实现

- 员工服务：三个入口、自然语言咨询、适用清单、原版本引用、自报缺失条件、请求预览与明确提交、原输入保留、同类请求提示、本人进度和补件、确认、重开、反馈。
- HR：事项/状态/城市/负责人/超期过滤、接单、公开回复与内部备注分栏、可编辑回复草稿、补问、IT 任务和依赖、按事项提交处理结果、取消；计划日期已变更提示。
- IT：仅列出指派任务的最小上下文，明确“演示任务已完成”。
- 制度：列表、草稿创建/编辑、正文预览、适用集合和日期、管理员发布、后续版本创建。
- 分析：按数据来源与日期筛选、样本数/分母/定义、计算明细、可追溯请求、缺项排行、建议草稿、CSV 导出。
- 桌面与 390px 手机布局；表单标签、键盘焦点、对话框焦点限制和 Escape 关闭、状态文本。

## 实际运行

2026-11-24，macOS，Node 24.13.0、npm 11.6.2、Google Chrome 158.0.8127.88、Playwright 1.56.1。

`npm run build --prefix frontend`：通过，执行 TypeScript 检查与 Vite 生产构建。日志见 `reports/frontend-build.txt`。

`cd frontend && npm run test:e2e`：3/3 通过（最终运行 11.4 秒），结果见 `reports/browser-tests.json`。

1. 实际浏览器完成员工查询、打开引用、创建账号异常、HR 接单建 IT 任务、阻止必需任务未完成时办结、IT 完成、HR 提交结果、员工确认，额外读取 API 断言最终状态 `closed`。
2. 首次缺预计日期的提交被拦截、输入保留、修改同一草稿后重交、HR 批准延期，最终验证材料 `provided=false` 且非必需后续任务仍为 `pending`。
3. 看板显示百分比与明细、知识草稿正文预览和保存、手机页面无文档横向溢出。

测试在单独回环端口 8001 和系统临时目录的独立 SQLite 库运行，写入 `data_origin=test`，不修改日常演示库。构建须先完成。macOS 配置优先使用已安装 Chrome；其他平台先执行 `npx playwright install chromium`。当前 webServer 命令使用 macOS/Linux `.venv/bin/python`；Windows 如需运行此浏览器测试，须改为 `.venv\\Scripts\\python.exe`，**尚未在 Windows 验证**。

初次浏览器运行发现接单请求返回时可能清空用户刚输入的新回复，已修复为仅清除当次提交的原内容，保留初次报告 `reports/browser-tests-initial.json`。后续一次运行的失败是后端材料状态由数值规范成布尔后测试仍断言 `0`，已改为 `false`；最终完整运行通过。

## 视觉检查

实际生成并查看了 `reports/employee-desktop.png`、`reports/employee-mobile.png`；`reports/analytics-desktop.png`、`reports/employee-confirmation.png` 同步生成。检查桌面布局、中文换行、滚动表格、手机文档宽度和状态标签。截图中的数字来自隔离模拟库，不是业务收益。

## 环境取舍

默认 npm registry 连接超时，现有用户 npm 缓存存在文件权限问题；使用公开 npm 镜像和临时独立缓存安装完成：

```sh
npm install --prefix frontend --registry=https://registry.npmmirror.com --cache=/private/tmp/onboardflow-npm-cache
```

没有更改用户全局 npm 配置，没有删除或覆盖用户缓存。后续通过锁定文件执行 `npm ci --prefix frontend`。

## 验证边界

这些是开发者执行的合成浏览器任务，不是用户可用性访谈；没有测屏幕阅读器、Firefox、Safari 或 Windows。表格在手机内横向滚动，文档本身不横向溢出。简易 Markdown 渲染支持标题、段落和列表，不执行嵌入 HTML。
