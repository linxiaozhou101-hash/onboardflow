# 数据字典

内部系统时间统一为带时区 UTC ISO 8601，页面显示 Asia/Shanghai。制度适用日期为 YYYY-MM-DD，自闭区间起点到不含终点 `[effective_from,effective_to)`。各表 DDL 的唯一来源是 `backend/app/db.py` 与 `knowledge.py`；下表说明业务含义。

|表|核心字段|含义与约束|
|---|---|---|
|users|id, role, company, city, employment_type, planned_start_date, group_name|服务端身份和已验证资料；聊天不能覆盖|
|sessions|id, user_id, csrf, created_at|本地演示会话与修改令牌|
|query_context|user_id, context|仅补原本为空的业务条件，不提升角色/企业权限|
|cases|id, case_number, requester_id, category, status, assigned_group, assigned_to|唯一 HR 主负责人，IT 子任务不转移主责|
|cases|structured_fields, policy_snapshot_refs, context_source|申请字段、历史依据及已验证/自报来源|
|cases|created_at, due_at, sla_hours, first_human_response_at, latest_resolved_at, closed_at, cancelled_at|到期快照、人工公开首次响应、最新解决与员工确认分开|
|cases|resolution_code, reopen_count, idempotency_key, row_version, data_origin|审批结论、累计重开、幂等与并发版本、数据来源|
|drafts|id, requester_id, draft_version, fields, confirmation_token, case_id|草稿不创建工单；修改产生版本，员工确认后关联工单|
|submission_attempts|id, draft_id, requester_id, attempt_no, attempted_at, validation_result, missing_field_codes, idempotency_key|主动确认提交时持久化，即使缺项阻断；同一次网络重试唯一键去重|
|materials|required_item_code, provided, format_check_status, supplied_at|只表示模拟元信息和格式；不核验文件真伪|
|tasks|case_id, type, assignee_id, status, required, dependency_task_ids, due_at, completed_at, minimal_context, row_version|必需任务阻断解决；可选补交跟进可在申请处理完后继续|
|messages|case_id, actor_id, body, visibility, created_at|public 员工可见；internal 仅 HR，不进入员工序列化|
|events|kind, actor_id, case_id, metadata, visibility, occurred_at, data_origin|业务动作与分析事件；咨询 ID 在 metadata|
|policies|policy_key, version, cities, employment_types, visibility_roles, status, effective_from, effective_to, content_markdown, content_hash|草稿、审核发布、范围与原文；保留旧版本审计|
|feedback|case_id/conversation_id, user_id, ease, outcome, created_at, data_origin|1–5 分及 resolved/unresolved/uncertain；每个对象一次|
|settings|key, value|虚构 SLA 与演示配置|

`data_origin`：`demo` 固定模拟业务数据，`manual` 人工试用数据，`test` 自动测试数据。自动验收使用独立临时 SQLite 数据库，不写入默认看板。首次完整率包含失败尝试，未提交草稿不计入。统计没有以文本日志代替结构化事件。

## 事件字典

|事件|触发|统计用途|
|---|---|---|
|question_submitted|用户提交咨询|唯一 conversation_id 数和消息数|
|context_requested|需要补充适用条件|条件缺口|
|answer_shown|呈现一次答案/人工路径|provider_mode 与转人工原因|
|case_draft_created|保存结构化草稿|追溯，未确认不算提交|
|case_submitted|确认且建单成功|建单记录|
|employee_message|员工补充或主动询问进度|kind=progress_inquiry 才计重复联系|
|hr_public_response|HR 明确发送员工可见内容|首次人工响应|
|status_changed|合法状态流转|处理时间线|
|task_created/task_completed|事务内建立/完成任务|依赖与处理证据|
|knowledge_published|管理员审核发布|知识版本追溯|
|feedback_submitted|员工提交有效评价|反馈机会与回复率|
