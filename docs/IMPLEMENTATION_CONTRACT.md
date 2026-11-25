# Implementation contract (internal)
Python imports backend.app.*. SQLite stdlib, FastAPI/Pydantic. db.py exports connect() (sqlite Row), tx() contextmanager BEGIN IMMEDIATE commit/rollback, now() injectable UTC ISO string, uid(prefix), dump(obj), unpack(row) decodes JSON columns; error(code,message,status=400) raises HTTPException detail={code,message}; log(conn,kind,actor_id,case_id=None,metadata=None,visibility='public',data_origin='demo',occurred_at=None). auth.py exports user(request) dependency returning dict, require_hr(u), can_case(u,c), get_case(conn,id,u). roles employee/hr/admin/it. IDs emp01..emp08, hr01/hr02, admin01,it01/it02. company='xingzhou', group Guangzhou='广州HR', Shenzhen='深圳HR'. City 广州/深圳, employment_type 正式员工/实习生.

Schema core:
users(id,name,role,company,city,employment_type,planned_start_date,group_name)
sessions(id,user_id,csrf,created_at)
cases(id,case_number,requester_id,category,status,assigned_group,assigned_to,description,structured_fields JSON,policy_snapshot_refs JSON,context_source JSON,created_at,updated_at,first_human_response_at,due_at,latest_resolved_at,closed_at,cancelled_at,resolution_code,reopen_count, idempotency_key,row_version,sla_hours,data_origin)
tasks(id,case_id,type,assignee_id,status,required integer,dependency_task_ids JSON,due_at,completed_at,minimal_context JSON,row_version)
messages(id,case_id,actor_id,body,visibility,created_at)
events(id,kind,actor_id,case_id,metadata JSON,visibility,occurred_at,data_origin)
materials(id,case_id,required_item_code,provided integer,format_check_status,supplied_at)
drafts(id,requester_id,category,draft_version,context_source JSON,fields JSON,created_at,updated_at,case_id,confirmation_token)
submission_attempts(id,draft_id,requester_id,attempt_no,attempted_at,validation_result,missing_field_codes JSON,idempotency_key,payload_hash,result_case_id,data_origin) UNIQUE(requester_id,idempotency_key)
feedback(id,case_id,conversation_id,user_id,ease,outcome,created_at,data_origin) UNIQUE(user_id,case_id) and unique conversation
query_context(user_id PRIMARY KEY,context JSON)
settings(key PRIMARY KEY,value JSON)

Root owns db.py auth.py cases.py main.py seed.py (users/cases) tests/test_cases.py README scripts startup. Knowledge agent owns knowledge.py providers.py knowledge seed via seed_knowledge(conn), tests/test_knowledge.py. Knowledge router full /api paths. policy table created with init_knowledge(conn) called db.init_db. exports applicable(conn,u,context=None) -> list policy dict; resolve_context(conn,u)->dict includes city/employment_type/planned_start_date (document source keys), citations_for(conn,u,category)->list optional root integration. Query response standard required action/answer_text/required_context_fields/citation_refs/draft_case/reason_code/provider_mode/retrieval_trace_id plus optional context/checklist/cases. GET /api/checklist. Query body {question,conversation_id?}. POST /api/assistant/context {city?,employment_type?,planned_start_date?}. Citation link /api/knowledge/{id}. knowledge own schemas.

Root API:
GET /api/demo/accounts -> array user dict; POST /api/demo/login {user_id}-> {user,csrf_token}, cookie session. GET /api/me same. mutating authenticated calls require X-CSRF-Token. GET /api/health.
GET /api/cases -> array; GET /api/cases/{id} -> case merged {messages,events,tasks,materials,requester:{name,city,...}}. Can filter category,status,city,assigned_to,overdue; employee sees own.
POST /api/cases/draft {category,fields:{description,missing_item?,expected_date?,device_type?},draft_id?} -> draft + existing_cases + confirmation_token. Saving draft version increment. POST /api/cases {draft_id,draft_version,confirmation_token,confirmed:true,allow_duplicate:false} Idempotency-Key header -> case. Missing rules returns HTTP422 detail {code:'missing_fields',message,missing_fields,attempt_id} persisted first. Required description all; missing_item & expected_date MATERIAL_EXCEPTION; device_type ACCESS_DEVICE.
POST /api/cases/{id}/actions/{action} body {row_version,body?,reason?,resolution_code?,expected_date?,assignee_id?,task_type?,required?,dependency_task_ids?}. Actions accept/ask_employee/create_task/resolve/confirm/reopen/cancel. task_type IT_ACCESS|IT_DEVICE; create_task only in_progress. resolve MATERIAL_EXCEPTION needs approved_deferred|rejected, body, expected_date for deferred; automatically optional MATERIAL_FOLLOWUP, materials unchanged. GENERAL requires body and policy_snapshot_refs. ACCESS requires >=1 required IT task all completed. POST messages {row_version,body,visibility:'public'|'internal',kind?:'progress_inquiry'}; employee messages waiting_employee -> in_progress. POST materials {row_version,required_item_code,provided:true}; accepts resolved/closed for optional followup.
GET /api/tasks -> minimal task array with context; POST /api/tasks/{id}/actions/complete {row_version,body?}.
Analytics agent owns analytics.py router and seed-independent tests/test_metrics.py; GET /api/analytics/summary, details, export; feedback POST route; docs deliverables & evaluation datasets/script. Coordinate schemas from this document. Seed cases root will have 72 with legal transitions events, data_origin demo; evaluation tests use isolated db.

Frontend owner all frontend only. API errors detail code/message; dates UTC render Asia/Shanghai. Login screen choose account, role routes /employee, /hr/cases, /it/tasks, /hr/knowledge,/hr/analytics. Use API contract above and coordinate agent knowledge/metrics endpoints. Node24 supports vite7. Use vanilla lightweight charts no need graph lib.
