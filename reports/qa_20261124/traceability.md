# 关键需求与验证证据

以下是任务书C1–C19关键业务检查的追溯表。每行覆盖列出的具体条件，不代表所有可能输入、100%代码覆盖或生产安全认证。所有API测试使用隔离数据库。

|需求|测试证据（backend/tests，除非另注）|
|---|---|
|C1 无Key三类任务|test_cases.py；test_knowledge.py；frontend/tests/workflow.spec.ts|
|C2 广州正式/深圳实习清单|test_knowledge::test_city_and_employment_checklists；qa/test_policy_metrics日期/范围矩阵|
|C3 缺条件及身份不可伪造|test_knowledge缺条件/伪造身份；qa/test_security外层字段注入和自报路由|
|C4 未发布/过期/越权/半开区间|test_knowledge日期边界；qa/test_policy_metrics发布时区、各范围端点|
|C5 当前版本刷新/旧引用追溯|test_knowledge版本衔接；qa/test_policy_metrics历史访问范围复查和不可修改已发布正文|
|C6 无依据/冲突转人工|test_knowledge候选全集冲突；qa/test_policy_metrics错误段落及撤回期间泛词误匹配|
|C7 草稿/确认/幂等|test_cases幂等；qa/test_security严格确认类型/上下文漂移/8客户端重试；load_results16路重试|
|C8 员工对象/内部备注|test_cases权限隔离；qa/test_security读写权限矩阵；浏览器跨身份迟到响应测试|
|C9 IT最小视图|test_cases IT全流程；test_integrity导入公司边界；qa/test_security仅指派IT可操作|
|C10 必需任务约束|test_cases前置依赖；qa/test_security状态决策表；原浏览器流程的提前办结拦截|
|C11 人工响应/取消/等待/重开|test_metrics独立夹具；qa/test_policy_metrics标量预期及重开反馈不消失|
|C12 并发/回滚|test_integrity双命令竞争；test_cases回滚无孤立待办；qa/test_security并发首次失败尝试|
|C13 重启持久化|test_cases重启；load_results进程SIGKILL后会话、工单、消息/时间线恢复与DB完整性|
|C14 指标变更与复算|test_metrics实际数据库修改；qa/test_policy_metrics独立随机夹具、分派组一致性|
|C15 模型失效|test_knowledge4类故障；qa/test_resilience6类坏输出、慢服务隔离及争用会话|
|C16 全集/部分交集冲突|test_knowledge发布交集；qa/test_policy_metrics穷举集合预期；冲突不依赖Top-k|
|C17 延期不等于收齐|test_cases延期/补交；qa/test_security审批重开拒绝后材料真值；浏览器实际延期流程|
|C18 取消/重开/自报权限|test_cases取消联动/重开保留任务；test_integrity自报日期；qa/test_policy_metrics历史缓存权限|
|C19 首次失败纳入分母|test_cases失败持久化；test_metrics实时看板；qa/test_security并发失败与修正重提；真实浏览器丢失响应重试|

新增浏览器质量检查见browser.md：逐字键盘输入、焦点边界、实际颜色对比度抽查、320/390宽度、日期筛选时区、失效数据清空、并发冲突可恢复。依赖报告独立于功能通过数。
