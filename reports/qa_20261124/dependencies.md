# 锁定依赖检查与修复

查询源：OSV公开漏洞数据库，脚本 `scripts/qa_dependencies.py`。仅发送公开依赖包名及版本，不发送源码、密钥或业务记录。结果是“包版本与已知公告匹配”，不是每条公告均已在本应用上利用成功。

初轮检查144个锁定包，匹配28条公告记录，按公告别名去重为20组，涉及Starlette、Vite和pytest三个包。初始JSON含完整公告、受影响区间和引用，保留在dependency_audit_initial.json。

**DEP-01 / P2：已知受影响的依赖版本仍被锁定。** 对本项目可达性分别判断：Starlette静态文件/FileResponse用于实际页面交付；Vite问题主要涉及开发服务，pytest仅用于测试。未开放公网，不把全部公告都声称为当前服务可利用漏洞。

|依赖|原版本|最终锁定版本|说明|
|---|---|---|---|
|FastAPI|0.115.12|0.165.0|为修复后的Starlette提供兼容依赖范围，PyPI实查要求Starlette>=1.6.0|
|Starlette|0.46.2|1.10.0|修复包括静态响应Range处理等已知问题|
|pytest|9.0.1|9.2.0|覆盖临时目录处理修复|
|Vite|6.3.5|6.4.5|保持6.x；6.4.4中间补丁复查又发现独有公告，继续升6.4.5|

中间复查发现Vite6.4.4专有的GHSA-8g5g-mxqh-grgj（修复版6.4.5），记录dependency_audit_intermediate.json；没有把第一轮升级直接视为结束。

最终在2026-11-24实际查询146个锁定包，**0条匹配的已知漏洞记录**，见dependency_audit.json。这不保证未知漏洞不存在，结论限于查询时点与数据库覆盖。

升级后`pip check`无依赖冲突，后端151项、浏览器17项及构建完成回归。只余Starlette TestClient对httpx兼容方式的1条弃用提示；未当作安全漏洞或静默隐藏。

官方公告交叉核对：[Starlette Range处理](https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8)、[Vite开发服务路径处理](https://github.com/vitejs/vite/security/advisories/GHSA-4w7w-66w2-5vf9)。完整其他公告来源见JSON的references字段。
