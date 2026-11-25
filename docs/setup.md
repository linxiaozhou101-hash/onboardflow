# 启动与配置

[返回项目介绍](../README.md)

## 环境

已实际验证：macOS、Python 3.14.2、Node 24.13.0、npm 11.6.2。推荐 Python 3.11–3.14、Node 22 或 24。Linux 和 Windows 提供参考命令，尚未实机验证。

首次安装依赖需要联网；安装完成后默认使用离线规则检索和回答，无需模型服务或 API Key。

## 安装并启动：macOS / Linux

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
cp .env.example .env
.venv/bin/python scripts/init_db.py
.venv/bin/python scripts/run.py
```

已有 `.env` 时保留原配置，不必再次复制。打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。构建后的前端和 API 由同一个进程提供服务；启动器只监听本机回环地址。默认数据库为 `data/onboardflow.sqlite3`，重启后数据保留。

API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## Windows PowerShell

在仓库根目录执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts\init_db.py
.\.venv\Scripts\python.exe scripts\run.py
```

打开同一个本地地址。Windows 命令尚未实机验证。

## 开发模式

保持后端运行，在另一个终端启动 Vite：

```bash
npm --prefix frontend run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)，Vite 将 `/api` 请求代理到 8000。只需运行一个后端进程。

## 全部演示账号

在首页选择身份；登录后也可使用“演示身份切换”。服务器建立 HttpOnly 会话并校验权限，演示账号无需密码。

| 账号 | 身份与范围 |
| --- | --- |
| `emp01` | 广州正式员工 |
| `emp02` | 深圳正式员工 |
| `emp03` | 广州实习生 |
| `emp04` | 深圳实习生 |
| `emp05` / `emp06` | 广州 / 深圳正式员工 |
| `emp07` | 城市和计划日期尚缺，用于演示澄清 |
| `emp08` | 雇佣类型和计划日期尚缺，用于演示澄清 |
| `hr01` / `hr02` | 广州 / 深圳 HR 经办人，仅负责组 |
| `admin01` | HR 管理员，审核发布制度、查看服务分析 |
| `it01` / `it02` | 仅各自分配的 IT 演示任务 |

## 数据初始化与重置

`init_db.py` 可重复执行，不会覆盖已有数据库。首次初始化包括 8 位员工、5 位经办/管理员、16 条政策版本、72 个历史请求及流转事件。种子使用固定基准时间 `2026-11-24T02:00:00Z`；服务端按实际时间运行，因此未结案年龄等指标会随日期变化。

停止服务后，可重置默认演示库：

```bash
.venv/bin/python scripts/init_db.py --reset-demo --confirm RESET_DEMO
```

该命令会删除默认库中的已有操作记录并重建模拟数据，仅作用于 `data/onboardflow.sqlite3`，不允许重置自定义 `DATABASE_PATH`。Windows 对应命令：

```powershell
.\.venv\Scripts\python.exe scripts\init_db.py --reset-demo --confirm RESET_DEMO
```

数据来源分为三类，分析页可切换：

| 来源 | 用途 |
| --- | --- |
| `demo` | 初始化的模拟历史 |
| `manual` | 默认交互操作创建的新咨询与请求 |
| `test` | 自动测试记录 |

修改已有模拟记录仍保留其来源。自动化测试使用临时数据库，与默认演示库分开。

## 环境变量

`scripts/run.py` 读取仓库根目录 `.env`。系统环境变量优先于文件配置；自定义数据库也可通过系统环境变量传给初始化脚本。不要把 API Key 提交到仓库。

| 变量 | 默认值或用途 |
| --- | --- |
| `APP_MODE` | `demo`；其他值禁用快捷登录 |
| `BIND_HOST` | `127.0.0.1`，启动器仅允许回环地址 |
| `DATABASE_PATH` | `data/onboardflow.sqlite3` |
| `LLM_MODE` | `offline`；`remote` 启用可选模型归纳适配器 |
| `LLM_API_KEY` / `LLM_ENDPOINT` / `LLM_MODEL` | 远程模式的服务端配置；`LLM_ENDPOINT` 为完整的 chat-completions URL |
| `DATA_ORIGIN` | `manual`；测试环境使用 `test` |
| `COOKIE_SECURE` | `false`，适配本地 HTTP；HTTPS 环境应为 `true` |
| `ALLOWED_ORIGINS` | 本机 8000/5173 的 `localhost` 与 `127.0.0.1` 来源白名单 |

远程模式在本地规则答案之外生成额外草稿：输出须符合 schema，引用须属于获准片段，失败时回退离线结果。模型不能审批、改权限或直接创建工单。真实模型服务尚未测试，相关设计见 [制度检索与版本](retrieval.md)。

## 复现检查

在仓库根目录执行：

```bash
# 后端回归
.venv/bin/python -m pytest backend/tests -q

# 公开合成验收
.venv/bin/python scripts/evaluate.py

# 构建及原浏览器回归
npm --prefix frontend run build
npm --prefix frontend run test:e2e

# 七条独立角色旅程
npm --prefix frontend run test:e2e -- --config playwright.roleplay.config.ts
```

浏览器配置在 macOS 检测到系统 Google Chrome 时使用该浏览器，其他环境使用 Playwright Chromium。首次安装 Chromium：

```bash
cd frontend
npx playwright install chromium
```

安装后回到仓库根目录运行上述检查。默认浏览器回归自动启动 8001 端口的隔离后端；角色演练使用 8004，均创建临时 SQLite，无需手动启动测试后端，也不使用 8000 演示库。默认回归不混入角色演练，两个命令分别运行。

Windows 运行浏览器测试时，需要将相应 `frontend/playwright*.config.ts` 中的 `../.venv/bin/python` 改为 `../.venv/Scripts/python.exe`；此方式尚未实机验证。

合成数据位于 `data/eval_dev` 与 `data/eval_acceptance`，执行结果输出到 `reports/`。现有角色脚本依赖 2026/2027 模拟制度日历，跨年后需同步调整情境日期；复跑可能覆盖同名报告，保留旧证据时先归档。

## 实测记录

- [角色拟真测试及最新回归](../reports/roleplay_20261124/README.md)：159 项后端、17 项浏览器回归、7 条角色旅程通过。
- [专业测试报告](../reports/qa_20261124/README.md)：包含 60 条合成验收、并发、恢复、权限、指标与依赖检查。
- [首轮验证](../reports/verification.md)：保留早期结果及修复过程。

依赖通过 `backend/requirements.lock` 和 `frontend/package-lock.json` 锁定。当前后端测试日志保留一条 TestClient/httpx 弃用提示，未影响通过；历史版本和警告记录保留在对应报告中。
