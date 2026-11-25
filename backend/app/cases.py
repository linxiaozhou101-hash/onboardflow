"""Business commands: one transaction per command, no writable status field."""
import hashlib,secrets,os,json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from .db import tx,unpack,now,uid,dump,error,log
from .auth import user,require_hr,get_case,can_case
from .schemas import CaseResponse,CaseDetail,RequestDraft,TaskResponse,ReplyDraft,ProfileChangeResponse
router=APIRouter()
CATEGORIES=('MATERIAL_EXCEPTION','ACCESS_DEVICE','GENERAL_ONBOARDING')
class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class DraftBody(Strict):
    category:Literal['MATERIAL_EXCEPTION','ACCESS_DEVICE','GENERAL_ONBOARDING']
    fields:dict
    draft_id:str|None=None
    draft_version:int|None=None
class SubmitBody(Strict):
    draft_id:str
    draft_version:int
    confirmation_token:str
    confirmed:StrictBool
    allow_duplicate:bool=False
class Action(Strict):
    row_version:int
    body:str=Field(default='',max_length=4000)
    reason:str=Field(default='',max_length=2000)
    resolution_code:str|None=None
    expected_date:str|None=None
    assignee_id:str|None=None
    task_type:Literal['IT_ACCESS','IT_DEVICE']|None=None
    task_instruction:str=Field(default='',max_length=1000)
    required:bool=True
    dependency_task_ids:list[str]=[]
class MessageBody(Strict):
    row_version:int
    body:str=Field(min_length=1,max_length=4000)
    visibility:Literal['public','internal']='public'
    kind:Literal['message','progress_inquiry']='message'
class MaterialBody(Strict):
    row_version:int
    required_item_code:str
    provided:Literal[True]=True

def version(ca,expected):
    if ca['row_version']!=expected:error('version_conflict','内容已被更新，请刷新后再操作',409)
def bump(c,id): c.execute('UPDATE cases SET row_version=row_version+1, updated_at=? WHERE id=?',(now(),id))
def transition(c,ca,u,status,reason=None):
    c.execute('UPDATE cases SET status=? WHERE id=?',(status,ca['id']))
    log(c,'status_changed',u['id'],ca['id'],{'from_status':ca['status'],'to_status':status,'reason':reason},data_origin=ca['data_origin'])
    ca['status']=status

def public_reply(c,ca,u,body,visibility='public'):
    if not body.strip():error('missing_message','请填写具体说明')
    c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',(uid('msg'),ca['id'],u['id'],body,visibility,now()))
    kind='internal_note' if visibility=='internal' else ('hr_public_response' if u['role'] in ('hr','admin') else 'employee_message')
    log(c,kind,u['id'],ca['id'],{'body':body},visibility,ca['data_origin'])
    if kind=='hr_public_response':c.execute('UPDATE cases SET first_human_response_at=COALESCE(first_human_response_at,?) WHERE id=?',(now(),ca['id']))
    if visibility=='public': log(c,'notification_recorded',u['id'],ca['id'],{'channel':'local','text':'站内记录，未发送真实消息'},data_origin=ca['data_origin'])

def detail(c,ca,u):
    result=dict(ca)
    result['requester']=unpack(c.execute('SELECT id,name,city,employment_type,planned_start_date FROM users WHERE id=?',(ca['requester_id'],)).fetchone())
    visible=" AND visibility='public'" if u['role']=='employee' else ''
    result['messages']=[unpack(r) for r in c.execute('SELECT * FROM messages WHERE case_id=?'+visible+' ORDER BY created_at,rowid',(ca['id'],))]
    result['events']=[unpack(r) for r in c.execute('SELECT * FROM events WHERE case_id=?'+visible+' ORDER BY occurred_at,rowid',(ca['id'],))]
    result['tasks']=[unpack(r) for r in c.execute('SELECT * FROM tasks WHERE case_id=?',(ca['id'],))]
    result['materials']=[unpack(r) for r in c.execute('SELECT * FROM materials WHERE case_id=?',(ca['id'],))]
    from .knowledge import resolve_context
    old=ca['context_source'].get('planned_start_date')
    if isinstance(old,dict):old=old.get('value')
    current=resolve_context(c,result['requester']).get('planned_start_date')
    result['profile_changed']=bool(old and current and old!=current)
    return result

def existing(c,u,cat):
    return [unpack(r) for r in c.execute("SELECT * FROM cases WHERE requester_id=? AND category=? AND status NOT IN ('closed','cancelled') ORDER BY created_at DESC",(u['id'],cat))]

def snapshot(c,u,category):
    from .knowledge import citations_for,resolve_context
    return citations_for(c,u,category),resolve_context(c,u)

def origin(): return os.getenv('DATA_ORIGIN','manual')
def business_today(): return datetime.fromisoformat(now()).astimezone(ZoneInfo('Asia/Shanghai')).date()
MATERIAL_ALIASES={'入职信息表':'onboarding_form','联系信息确认':'contact_confirmation','实习安排确认':'internship_arrangement','任职信息确认':'employment_information'}
def material_code(value):return MATERIAL_ALIASES.get(value,value)

@router.post('/api/cases/draft', response_model=RequestDraft)
def save_draft(body:DraftBody,u=Depends(user)):
    if u['role']!='employee':error('forbidden','请以员工身份提出请求',403)
    with tx() as c:
        from .knowledge import resolve_context
        context=resolve_context(c,u)
        if body.draft_id:
            d=unpack(c.execute('SELECT * FROM drafts WHERE id=? AND requester_id=?',(body.draft_id,u['id'])).fetchone())
            if not d or d['case_id']:error('invalid_draft','草稿不存在或已提交',409)
            if body.draft_version!=d['draft_version']:error('version_conflict','草稿已修改或缺少版本，请重新读取预览',409)
            c.execute('UPDATE drafts SET category=?,fields=?,draft_version=draft_version+1,context_source=?,updated_at=?,confirmation_token=? WHERE id=?',(body.category,dump(body.fields),dump(context),now(),secrets.token_urlsafe(24),d['id']))
            did=d['id']
        else:
            did=uid('draft')
            c.execute('INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?,?)',(did,u['id'],body.category,1,dump(context),dump(body.fields),now(),now(),None,secrets.token_urlsafe(24)))
            log(c,'case_draft_created',u['id'],metadata={'draft_id':did,'category':body.category},data_origin=origin())
        d=unpack(c.execute('SELECT * FROM drafts WHERE id=?',(did,)).fetchone())
        d['existing_cases']=existing(c,u,body.category)
        return d

def missing_fields(d):
    from .knowledge import RULES
    fields=d['fields']; required=RULES[d['category']]['required_fields']
    missing=[key for key in required if not isinstance(fields.get(key),str) or not fields[key].strip()]
    if fields.get('expected_date'):
        try:
            day=date.fromisoformat(fields['expected_date'])
            if day < business_today():missing.append('expected_date_in_past')
        except (ValueError,TypeError):missing.append('expected_date_invalid')
    return missing

@router.post('/api/cases', response_model=CaseDetail)
def submit(body:SubmitBody,idempotency_key:str=Header(alias='Idempotency-Key',min_length=8,max_length=160),u=Depends(user)):
    if u['role']!='employee':error('forbidden','请以员工身份提出请求',403)
    failure=None; result=None
    with tx() as c:
        d=unpack(c.execute('SELECT * FROM drafts WHERE id=? AND requester_id=?',(body.draft_id,u['id'])).fetchone())
        if not d:error('not_found','草稿不存在',404)
        payload_hash=hashlib.sha256(json.dumps(body.model_dump(),ensure_ascii=True,sort_keys=True).encode('ascii')).hexdigest()
        old=unpack(c.execute('SELECT * FROM submission_attempts WHERE requester_id=? AND idempotency_key=?',(u['id'],idempotency_key)).fetchone())
        if old:
            if old['payload_hash']!=payload_hash:error('idempotency_conflict','同一提交标识不能用于不同内容',409)
            if old['result_case_id']:return detail(c,get_case(c,old['result_case_id'],u),u)
            failure={'code':'missing_fields','message':'请补齐必填信息后重新预览提交','missing_fields':old['missing_field_codes'],'attempt_id':old['id']}
        else:
            if d['case_id']:error('draft_submitted','这个草稿已创建请求',409)
            if body.draft_version!=d['draft_version']:error('version_conflict','草稿已修改，请重新预览',409)
            if not body.confirmed or not body.confirmation_token.isascii() or not secrets.compare_digest(body.confirmation_token,d['confirmation_token']):error('confirmation_required','请先预览并确认请求',400)
            from .knowledge import resolve_context
            current_context=resolve_context(c,u)
            if current_context!=d['context_source']:error('context_changed','员工资料或查询条件已改变，请重新预览并确认请求',409)
            miss=missing_fields(d)
            if not miss and existing(c,u,d['category']) and not body.allow_duplicate:error('existing_case','已有同类未完结请求，请查看原请求，或明确确认这是新事项',409)
            aid=uid('attempt'); number=c.execute('SELECT COUNT(*) FROM submission_attempts WHERE draft_id=?',(d['id'],)).fetchone()[0]+1
            c.execute('INSERT INTO submission_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)',(aid,d['id'],u['id'],number,now(),'invalid' if miss else 'valid',dump(miss),idempotency_key,payload_hash,None,origin()))
            if miss:failure={'code':'missing_fields','message':'请补齐必填信息后重新预览提交','missing_fields':miss,'attempt_id':aid}
            else:
                caseid=uid('case'); group=(current_context.get('city') or '广州')+'HR'
                owner_row=c.execute("SELECT id FROM users WHERE role='hr' AND group_name=? AND company=? ORDER BY id",(group,u['company'])).fetchone()
                if not owner_row:error('assignment_unavailable','暂未配置负责团队，请联系 HR',409)
                owner=owner_row['id']
                sla=unpack(c.execute("SELECT value FROM settings WHERE key='sla_hours'").fetchone())['value'][d['category']]
                refs,ctx=snapshot(c,u,d['category']); stamp=now(); due=(datetime.fromisoformat(stamp)+timedelta(hours=sla)).isoformat()
                c.execute('INSERT INTO cases (id,case_number,requester_id,category,status,assigned_group,assigned_to,description,structured_fields,policy_snapshot_refs,context_source,created_at,updated_at,due_at,idempotency_key,sla_hours,data_origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(caseid,'OF-'+caseid[-8:].upper(),u['id'],d['category'],'new',group,owner,d['fields']['description'],dump(d['fields']),dump(refs),dump(ctx),stamp,stamp,due,idempotency_key,sla,origin()))
                ca=get_case(c,caseid,u)
                log(c,'case_submitted',u['id'],caseid,{'draft_id':d['id'],'category':d['category']},data_origin=origin())
                transition(c,ca,u,'assigned','规则分派')
                if d['category']=='MATERIAL_EXCEPTION':c.execute('INSERT INTO materials VALUES (?,?,?,?,?,?)',(uid('mat'),caseid,material_code(d['fields']['missing_item']),0,'missing',None))
                c.execute('UPDATE drafts SET case_id=? WHERE id=?',(caseid,d['id']))
                c.execute('UPDATE submission_attempts SET result_case_id=? WHERE id=?',(caseid,aid))
                result=detail(c,get_case(c,caseid,u),u)
    # Outside transaction: invalid first submissions must persist.
    if failure:raise HTTPException(422,detail=failure)
    return result

@router.get('/api/cases', response_model=list[CaseResponse])
def list_cases(category:str|None=None,status:str|None=None,city:str|None=None,assigned_to:str|None=None,overdue:bool=False,u=Depends(user)):
    if u['role']=='it':error('forbidden','请在 IT 待办查看已指派任务',403)
    with tx() as c:
        rows=[]
        for r in c.execute('SELECT cases.*,users.city,users.name AS requester_name FROM cases JOIN users ON users.id=cases.requester_id WHERE users.company=? ORDER BY cases.created_at DESC',(u['company'],)):
            ca=unpack(r)
            ca['city']=ca['context_source'].get('city') or ca['city']
            if not can_case(u,ca):continue
            if category and ca['category']!=category or status and ca['status']!=status or city and ca['city']!=city or assigned_to and ca['assigned_to']!=assigned_to:continue
            if overdue and not(ca['due_at']<now() and ca['status'] not in ('resolved','closed','cancelled')):continue
            rows.append(ca)
        return rows

@router.get('/api/cases/{id}', response_model=CaseDetail)
def get_detail(id:str,u=Depends(user)):
    with tx() as c:return detail(c,get_case(c,id,u),u)

def add_task(c,ca,u,typ,assignee,required,deps=None,instruction=''):
    deps=deps or []
    for dep in deps:
        if not c.execute('SELECT id FROM tasks WHERE id=? AND case_id=?',(dep,ca['id'])).fetchone():error('invalid_dependency','前置任务必须属于同一请求')
    target=unpack(c.execute('SELECT * FROM users WHERE id=?',(assignee,)).fetchone())
    if not target or target['company']!=u['company'] or (typ.startswith('IT_') and target['role']!='it'):error('invalid_assignee','请选择有效 IT 经办人')
    person=unpack(c.execute('SELECT id,name,city,planned_start_date FROM users WHERE id=?',(ca['requester_id'],)).fetchone())
    person['city']=person['city'] or ca['context_source'].get('city')
    person['planned_start_date']=person['planned_start_date'] or ca['context_source'].get('planned_start_date')
    taskid=uid('task');context={**person,'instruction':instruction.strip() or ('演示账号准备' if typ=='IT_ACCESS' else ('演示设备准备' if typ=='IT_DEVICE' else '跟进材料元信息'))}
    c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)',(taskid,ca['id'],typ,assignee,'pending',int(required),dump(deps),ca['due_at'],None,dump(context),1))
    log(c,'task_created',u['id'],ca['id'],{'task_id':taskid,'type':typ,'required':required},data_origin=ca['data_origin'])
    return taskid

@router.post('/api/cases/{id}/actions/{action}', response_model=CaseDetail)
def act(id:str,action:str,body:Action,u=Depends(user)):
    with tx() as c:
        ca=get_case(c,id,u);version(ca,body.row_version)
        st=ca['status']; role=u['role']
        if st=='cancelled':error('invalid_transition','已取消请求不能继续操作')
        if action in ('accept','ask_employee','create_task','resolve'):
            require_hr(u)
        if action=='accept':
            if st!='assigned':error('invalid_transition','只有待接单请求可以接单')
            transition(c,ca,u,'in_progress')
        elif action=='ask_employee':
            if st!='in_progress':error('invalid_transition','请先接单再补问')
            public_reply(c,ca,u,body.body);transition(c,ca,u,'waiting_employee')
        elif action=='create_task':
            if st!='in_progress':error('invalid_transition','处理中才可建立 IT 任务')
            add_task(c,ca,u,body.task_type or 'IT_ACCESS',body.assignee_id or 'it01',body.required,body.dependency_task_ids,body.task_instruction)
        elif action=='resolve':
            if st!='in_progress':error('invalid_transition','只有处理中请求可以完成')
            if ca['category']!='MATERIAL_EXCEPTION' and body.resolution_code not in (None,'answered'):
                error('invalid_resolution','此事项不适用材料补交审批结论')
            tasks=[unpack(r) for r in c.execute('SELECT * FROM tasks WHERE case_id=?',(id,))]
            if any(t['required'] and t['status']!='completed' for t in tasks):error('pending_tasks','仍有必需任务未完成')
            for t in tasks:
                if t['required'] and any(c.execute('SELECT status FROM tasks WHERE id=?',(dep,)).fetchone()['status']!='completed' for dep in t['dependency_task_ids']):error('pending_dependencies','前置任务尚未完成')
            if ca['category']=='ACCESS_DEVICE' and not any(t['required'] and t['type'].startswith('IT_') for t in tasks):error('missing_it_task','请先创建并完成必需 IT 任务')
            if ca['category']=='MATERIAL_EXCEPTION':
                if body.resolution_code not in __import__('backend.app.knowledge',fromlist=['RULES']).RULES['MATERIAL_EXCEPTION']['completion_codes']:error('missing_decision','请明确批准延期或拒绝并说明原因')
                fields=ca['structured_fields'];fields['approval_reason']=(body.reason or body.body).strip()
                if body.resolution_code=='approved_deferred':
                    if not ca['policy_snapshot_refs']:error('missing_basis','没有适用补交制度依据，请先核对条件与政策')
                    try:day=date.fromisoformat(body.expected_date or '')
                    except ValueError:error('invalid_date','请填写约定补交日期')
                    if day < business_today():error('invalid_date','约定日期不能早于今天')
                    fields['approved_expected_date']=body.expected_date
                    if c.execute('SELECT 1 FROM materials WHERE case_id=? AND provided=0',(id,)).fetchone() and not c.execute("SELECT 1 FROM tasks WHERE case_id=? AND type='MATERIAL_FOLLOWUP' AND status='pending'",(id,)).fetchone():
                        tid=add_task(c,ca,u,'MATERIAL_FOLLOWUP',ca['assigned_to'],False)
                        c.execute('UPDATE tasks SET due_at=? WHERE id=?',(body.expected_date+'T15:59:59+00:00',tid))
                c.execute('UPDATE cases SET structured_fields=? WHERE id=?',(dump(fields),id))
            if ca['category']=='GENERAL_ONBOARDING' and not ca['policy_snapshot_refs']:error('missing_basis','缺少适用制度依据，请先核对政策')
            public_reply(c,ca,u,body.body)
            c.execute('UPDATE cases SET latest_resolved_at=?,resolution_code=? WHERE id=?',(now(),body.resolution_code or 'answered',id))
            transition(c,ca,u,'resolved')
        elif action=='confirm':
            if role!='employee':error('forbidden','仅请求员工本人可确认',403)
            if st!='resolved':error('invalid_transition','请等待 HR 完成处理')
            transition(c,ca,u,'closed');c.execute('UPDATE cases SET closed_at=? WHERE id=?',(now(),id))
        elif action=='reopen':
            if role not in ('employee','hr','admin') or st not in ('resolved','closed'):error('invalid_transition','仅已处理或确认完成请求可以重开')
            if not body.reason.strip():error('missing_reason','请说明重开原因')
            public_reply(c,ca,u,body.reason)
            transition(c,ca,u,'in_progress',body.reason)
            c.execute('UPDATE cases SET reopen_count=reopen_count+1,closed_at=NULL WHERE id=?',(id,))
        elif action=='cancel':
            if st=='closed':error('invalid_transition','已确认完成请求不能取消')
            if not body.reason.strip():error('missing_reason','请说明取消原因')
            transition(c,ca,u,'cancelled',body.reason)
            c.execute('UPDATE cases SET cancelled_at=? WHERE id=?',(now(),id))
            c.execute("UPDATE tasks SET status='cancelled',row_version=row_version+1 WHERE case_id=? AND status!='completed'",(id,))
        else:error('unknown_action','不支持此操作',404)
        bump(c,id);return detail(c,get_case(c,id,u),u)

@router.post('/api/cases/{id}/messages', response_model=CaseDetail)
def message(id:str,body:MessageBody,u=Depends(user)):
    with tx() as c:
        ca=get_case(c,id,u);version(ca,body.row_version)
        if ca['status'] in ('cancelled','closed'):error('invalid_transition','此请求已结束；如需补充请先重开')
        if body.visibility=='internal':require_hr(u)
        public_reply(c,ca,u,body.body,body.visibility)
        if u['role']=='employee' and body.kind=='progress_inquiry':log(c,'progress_inquiry',u['id'],id,data_origin=ca['data_origin'])
        if u['role']=='employee' and body.kind!='progress_inquiry' and ca['status']=='waiting_employee':transition(c,ca,u,'in_progress')
        bump(c,id);return detail(c,get_case(c,id,u),u)

@router.post('/api/cases/{id}/materials', response_model=CaseDetail)
def supply(id:str,body:MaterialBody,u=Depends(user)):
    with tx() as c:
        ca=get_case(c,id,u);version(ca,body.row_version)
        if ca['status']=='cancelled':error('invalid_transition','已取消请求不能补交')
        row=c.execute('SELECT * FROM materials WHERE case_id=? AND required_item_code=?',(id,material_code(body.required_item_code))).fetchone()
        if not row:error('invalid_material','该材料不属于此请求')
        c.execute("UPDATE materials SET provided=1,format_check_status='metadata_valid',supplied_at=? WHERE id=?",(now(),row['id']))
        log(c,'material_supplied',u['id'],id,{'required_item_code':body.required_item_code,'verification':'仅检查元信息，未核验真伪'},data_origin=ca['data_origin'])
        if not c.execute('SELECT 1 FROM materials WHERE case_id=? AND provided=0',(id,)).fetchone():
            for task in c.execute("SELECT id FROM tasks WHERE case_id=? AND type='MATERIAL_FOLLOWUP' AND status='pending'",(id,)).fetchall():
                c.execute("UPDATE tasks SET status='completed',completed_at=?,row_version=row_version+1 WHERE id=?",(now(),task['id']))
                log(c,'task_completed',u['id'],id,{'task_id':task['id']},data_origin=ca['data_origin'])
        if ca['status']=='waiting_employee':transition(c,ca,u,'in_progress')
        bump(c,id);return detail(c,get_case(c,id,u),u)

@router.get('/api/tasks', response_model=list[TaskResponse])
def task_list(u=Depends(user)):
    if u['role']=='employee':error('forbidden','请在本人请求查看进度',403)
    with tx() as c:
        result=[]
        for row in c.execute('SELECT * FROM tasks ORDER BY due_at'):
            task=unpack(row)
            parent_company=c.execute('SELECT u.company FROM users u JOIN cases ca ON ca.requester_id=u.id WHERE ca.id=?',(task['case_id'],)).fetchone()
            if not parent_company or parent_company['company']!=u['company']:continue
            if u['role']=='it':
                if task['assignee_id']!=u['id'] or not task['type'].startswith('IT_'):continue
            else:
                ca=unpack(c.execute('SELECT * FROM cases WHERE id=?',(task['case_id'],)).fetchone())
                if not can_case(u,ca):continue
            result.append(task)
        return result

@router.post('/api/tasks/{id}/actions/complete', response_model=TaskResponse)
def complete_task(id:str,body:Action,u=Depends(user)):
    with tx() as c:
        t=unpack(c.execute('SELECT * FROM tasks WHERE id=?',(id,)).fetchone())
        if not t or u['role']!='it' or t['assignee_id']!=u['id'] or not t['type'].startswith('IT_'):error('not_found','任务不存在或无权访问',404)
        version(t,body.row_version)
        ca=unpack(c.execute('SELECT * FROM cases WHERE id=?',(t['case_id'],)).fetchone())
        owner_company=c.execute('SELECT company FROM users WHERE id=?',(ca['requester_id'],)).fetchone()
        if not owner_company or owner_company['company']!=u['company']:error('not_found','任务不存在或无权访问',404)
        if ca['status'] not in ('in_progress','waiting_employee') or t['status']!='pending':error('invalid_transition','父请求状态或任务状态不允许完成')
        if any(c.execute('SELECT status FROM tasks WHERE id=?',(dep,)).fetchone()['status']!='completed' for dep in t['dependency_task_ids']):error('pending_dependencies','请先完成前置任务')
        c.execute("UPDATE tasks SET status='completed',completed_at=?,row_version=row_version+1 WHERE id=?",(now(),id))
        log(c,'task_completed',u['id'],ca['id'],{'task_id':id,'result':'演示任务已完成'},data_origin=ca['data_origin'])
        bump(c,ca['id']);return unpack(c.execute('SELECT * FROM tasks WHERE id=?',(id,)).fetchone())

@router.get('/api/cases/{id}/reply-draft', response_model=ReplyDraft)
def reply_draft(id:str,u=Depends(user)):
    require_hr(u)
    with tx() as c:
        ca=get_case(c,id,u)
        body='已收到你的请求。我们正在核对适用制度与剩余事项，具体结果将在本请求中更新。'
        if ca['category']=='MATERIAL_EXCEPTION':body='已收到补交申请，请确认缺失材料及预计补交日期。本次申请尚待审批，允许申请不代表已批准，延期批准也不代表材料已收齐。'
        if ca['category']=='ACCESS_DEVICE':body='已收到账号/设备准备异常，HR 将协调 IT 演示任务，完成后请在本请求核对结果。'
        return {'body':body,'citation_refs':ca['policy_snapshot_refs'],'provider_mode':'offline','published':False}

class ProfileChange(Strict):
    planned_start_date:date
    expected_planned_start_date:str|None
    reason:str=Field(min_length=1,max_length=1000)

@router.post('/api/employees/{id}/profile', response_model=ProfileChangeResponse)
def amend_profile(id:str,body:ProfileChange,u=Depends(user)):
    require_hr(u)
    with tx() as c:
        employee=unpack(c.execute("SELECT * FROM users WHERE id=? AND role='employee' AND company=?",(id,u['company'])).fetchone())
        if not employee or (u['role']=='hr' and employee['city']!=u['city']):error('not_found','员工不存在或不在负责范围',404)
        if employee['planned_start_date']!=body.expected_planned_start_date:error('version_conflict','员工资料已修改，请刷新',409)
        c.execute('UPDATE users SET planned_start_date=? WHERE id=?',(body.planned_start_date.isoformat(),id))
        log(c,'profile_amended',u['id'],metadata={'employee_id':id,'previous':employee['planned_start_date'],'planned_start_date':body.planned_start_date.isoformat(),'reason':body.reason},visibility='internal',data_origin=origin())
        return {'id':id,'planned_start_date':body.planned_start_date.isoformat(),'message':'适用清单将在下次查询时重算，旧工单保留原依据并提示核对。'}
