"""Recomputable operational metrics; no stored dashboard counters."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from io import StringIO
import csv
import sqlite3
from typing import Literal
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, model_validator
from . import db
from .auth import user, require_hr, can_case, get_case

router = APIRouter()


def dt(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def hours(a, b):
    return round((dt(b) - dt(a)).total_seconds() / 3600, 4)


def in_window(value, start, end):
    return bool(value) and dt(start) <= dt(value) < dt(end)


def calculate(cases, attempts, events, feedback, start, end, as_of):
    """Pure calculation with explicit clock and inputs, also used by independent fixtures."""
    rows, metrics = {}, {}
    current = [c for c in cases if dt(c['created_at']) <= dt(as_of)]
    cohort = [c for c in current if in_window(c['created_at'], start, end)]
    case_ids = {c['id'] for c in current}
    window_events = [e for e in events if in_window(e['occurred_at'], start, end) and dt(e['occurred_at']) <= dt(as_of)]
    questions = [e for e in window_events if e['kind'] == 'question_submitted']
    conversations = {e['metadata'].get('conversation_id') for e in questions} - {None}
    rows['consultations'] = questions
    rows['question_messages'] = questions
    def count(key, value, definition, source):
        rows[key] = source
        metrics[key] = dict(value=value, sample_size=len(source), unit='个', definition=definition, details_key=key)
    def rate(key, successes, sources, definition):
        rows[key] = sources
        metrics[key] = dict(value=round(successes / len(sources), 6) if sources else None,
                            numerator=successes, denominator=len(sources), sample_size=len(sources),
                            unit='比例', definition=definition, details_key=key)
    def mean(key, sources, definition):
        rows[key] = sources
        metrics[key] = dict(value=round(sum(x['hours'] for x in sources) / len(sources), 4) if sources else None,
                            sample_size=len(sources), unit='小时', definition=definition, details_key=key)
    count('consultations', len(conversations), '窗口内唯一咨询会话数；同一会话多条问题只计一次。', questions)
    count('question_messages', len(questions), '窗口内提交咨询问题的条数，不等于人数。', questions)
    first = {}
    for a in sorted(attempts, key=lambda a: (a['attempt_no'], a['attempted_at'])):
        first.setdefault(a['draft_id'], a)
    first = [a for a in first.values() if in_window(a['attempted_at'], start, end) and dt(a['attempted_at']) <= dt(as_of)]
    rate('first_submission_completeness', sum(a['validation_result'] in ('valid', 'complete', 'passed', 'success') for a in first), first,
         '每个有必填规则草稿的首次主动确认完整数 / 首次提交草稿数；包含被缺项拦截且未建单的尝试。网络重试去重。')
    responded = [{**c, 'hours': hours(c['created_at'], c['first_human_response_at'])} for c in cohort if c.get('first_human_response_at')]
    mean('first_response_hours', responded, '窗口内建单且已有首次员工可见 HR 回复的平均小时；内部备注与机器人回复不计。')
    waiting = [{**c, 'hours': hours(c['created_at'], as_of)} for c in cohort if not c.get('first_human_response_at')]
    mean('awaiting_first_response_hours', waiting, '窗口内建单且尚无公开人工回复的等待时长；不作为零响应时间。')
    solved = [{**c, 'hours': hours(c['created_at'], c['latest_resolved_at'])} for c in current
              if c['status'] in ('resolved','closed') and in_window(c.get('latest_resolved_at'), start, end)]
    mean('resolution_hours', solved, '最新解决发生于窗口且当前仍为待确认/已完成，从原始创建到最新解决的平均自然小时。')
    open_cases = [{**c, 'hours': hours(c['created_at'], as_of)} for c in current if c['status'] not in ('resolved','closed','cancelled')]
    mean('open_age_hours', open_cases, '统计时点所有未解决、未取消请求年龄，不限于本窗新建；等待与重开不暂停计时。')
    due = [c for c in current if c['status'] != 'cancelled' and in_window(c['due_at'], start, end) and dt(c['due_at']) <= dt(as_of)]
    successes = sum(c['status'] in ('resolved','closed') and bool(c.get('latest_resolved_at')) and dt(c['latest_resolved_at']) <= dt(c['due_at']) for c in due)
    rate('on_time_resolution', successes, due, '窗口内已到期非取消请求中，当前仍已解决且最新解决不晚于原到期时间的比例。')
    count('confirmed', sum(c['status'] == 'closed' for c in cohort), '窗口内新建请求中，截至统计时点经员工确认完成的请求数。', [c for c in cohort if c['status'] == 'closed'])
    count('cancelled', sum(c['status'] == 'cancelled' for c in cohort), '窗口内新建请求中已取消数；取消不是解决。', [c for c in cohort if c['status'] == 'cancelled'])
    count('reopened', sum(c.get('reopen_count',0) for c in cohort), '窗口内新建请求的累计重开次数；不重置创建和 SLA 到期时间。', [c for c in cohort if c.get('reopen_count',0)])
    # Feedback opportunities: one per answered conversation and one per latest resolved request in window.
    answer_ids = {e['metadata'].get('conversation_id') for e in window_events if e['kind'] == 'answer_shown'} - {None}
    # A later reopening must not erase an opportunity or its negative feedback.
    # Completion/SLA still depend on the current state; feedback is a historical
    # observation of a result that was actually delivered.
    feedback_cases = [c for c in current if in_window(c.get('latest_resolved_at'), start, end)
                      and dt(c['latest_resolved_at']) <= dt(as_of)]
    eligible = {('case', c['id']) for c in feedback_cases} | {('conversation', x) for x in answer_ids}
    valid_feedback = [f for f in feedback if dt(f['created_at']) <= dt(as_of) and
                      (('case', f.get('case_id')) in eligible or ('conversation', f.get('conversation_id')) in eligible)]
    valid_keys = {('case', f['case_id']) if f.get('case_id') else ('conversation', f['conversation_id']) for f in valid_feedback}
    rate('feedback_response', len(valid_keys), [{'kind':k,'id':v} for k,v in sorted(eligible)], '本窗口有回答的会话及出现最新解决事件的请求各产生一次反馈机会，后续重开不删除反馈；截至统计时点有效回复 / 应反馈数。未回复不等于满意。')
    rows['ease_score'] = valid_feedback
    metrics['ease_score'] = dict(value=round(sum(f['ease'] for f in valid_feedback)/len(valid_feedback),2) if valid_feedback else None,
                                sample_size=len(valid_feedback), unit='分（1–5）', definition='有效反馈的容易程度均分；解决判断单独汇总。', details_key='ease_score')
    contacts = [e for e in window_events if (e['kind'] == 'progress_inquiry' or (e['kind'] == 'employee_message' and e['metadata'].get('kind') == 'progress_inquiry')) and e.get('case_id') in case_ids]
    count('repeat_contacts', len(contacts), '窗口内同一请求的员工主动进度询问事件数，不是重开率。', contacts)
    def breakdown(values):
        return [{'key':k,'count':v} for k,v in Counter(values).most_common()]
    gaps = [e for e in window_events if e['kind'] in ('answer_shown','context_requested') and e['metadata'].get('reason_code') not in (None,'','matched','answered','ok','status_found','supported','existing_case')]
    rows['knowledge_gaps'] = gaps
    # Categories count questions; handoff reasons count final answer/context events.
    breakdowns = dict(categories=breakdown(e['metadata'].get('category','OTHER') for e in questions),
                      handoff_reasons=breakdown(e['metadata']['reason_code'] for e in gaps),
                      missing_fields=breakdown(f for a in first for f in a.get('missing_field_codes', [])),
                      missing_items=breakdown(c.get('structured_fields',{}).get('missing_item','未记录') for c in cohort if c.get('category')=='MATERIAL_EXCEPTION'),
                      statuses=breakdown(c['status'] for c in cohort),
                      provider_modes=breakdown(e['metadata'].get('provider_mode','offline') for e in window_events if e['kind']=='answer_shown'),
                      feedback_outcomes=breakdown(f['outcome'] for f in valid_feedback))
    rows.update(categories=questions,handoff_reasons=gaps,missing_fields=first,statuses=cohort,provider_modes=[e for e in window_events if e['kind']=='answer_shown'])
    rows['missing_items'] = [c for c in cohort if c.get('category')=='MATERIAL_EXCEPTION']
    reason_labels={'no_match':'未找到适用依据','missing_context':'必要条件未齐','policy_conflict':'制度范围冲突','profile_mismatch':'员工资料待核对','out_of_scope':'超出服务范围'}
    suggestions = [{'title':f'补充知识说明：{reason_labels.get(r["key"],r["key"])}', 'body':f'此窗口出现 {r["count"]} 次{reason_labels.get(r["key"],r["key"])}。建议 HR 查看原始事件，核对适用条件与制度空白后编辑知识草稿。', 'status':'draft'} for r in breakdowns['handoff_reasons'][:3]]
    return {'metrics':metrics,'breakdowns':breakdowns,'knowledge_suggestions':suggestions}, rows


def collect(conn, u, start=None, end=None, data_origin='demo'):
    require_hr(u)
    if data_origin not in ('demo','manual','test'):
        db.error('invalid_origin', '请选择模拟、人工试用或自动测试数据。')
    as_of = db.now()
    try:
        end = dt(end).isoformat() if end else (dt(as_of)+timedelta(microseconds=1)).isoformat()
        start = dt(start).isoformat() if start else (dt(as_of)-timedelta(days=30)).isoformat()
        if dt(start) >= dt(end): raise ValueError()
        if dt(end) > dt(as_of)+timedelta(days=1): raise ValueError()
    except (ValueError, TypeError):
        db.error('invalid_window','统计起止日期不正确，结束日期采用左闭右开的边界。')
    users = {row['id']:db.unpack(row) for row in conn.execute('SELECT * FROM users WHERE company=?',(u['company'],))}
    visible_users = {key for key,x in users.items() if u['role']=='admin' or x.get('group_name')==u.get('group_name') or (x['role']=='employee' and x.get('city')==u.get('city'))}
    cases = [db.unpack(r) for r in conn.execute('SELECT * FROM cases WHERE data_origin=?',(data_origin,))]
    cases = [c for c in cases if c['requester_id'] in users and can_case(u,c)]
    case_ids = {c['id'] for c in cases}
    drafts = {r['id']:db.unpack(r) for r in conn.execute('SELECT * FROM drafts') if r['requester_id'] in users}
    def visible_draft(draft):
        if not draft:
            return False
        if draft['case_id']:
            return draft['case_id'] in case_ids
        city = draft['context_source'].get('city') or users[draft['requester_id']].get('city') or '广州'
        return u['role']=='admin' or city+'HR'==u['group_name']
    attempts = [db.unpack(r) for r in conn.execute('SELECT * FROM submission_attempts WHERE data_origin=?',(data_origin,)) if visible_draft(drafts.get(r['draft_id']))]
    def visible_event(event):
        if event['case_id']:
            return event['case_id'] in case_ids
        if event['actor_id'] not in users:
            return False
        metadata = event['metadata']
        if metadata.get('scope_company',u['company']) != u['company']:
            return False
        if u['role']=='admin':
            return True
        if metadata.get('assigned_group'):
            return metadata['assigned_group']==u['group_name']
        if metadata.get('draft_id'):
            return visible_draft(drafts.get(metadata['draft_id']))
        # Legacy seed events predate scope snapshots; use their verified profile.
        return event['actor_id'] in visible_users
    all_events = [db.unpack(r) for r in conn.execute('SELECT * FROM events WHERE data_origin=?',(data_origin,))]
    events = [event for event in all_events if visible_event(event)]
    visible_conversations = {event['metadata'].get('conversation_id') for event in events if event['kind'] in ('answer_shown','question_submitted')}
    feedback = [db.unpack(r) for r in conn.execute('SELECT * FROM feedback WHERE data_origin=?',(data_origin,))
                if r['user_id'] in users and (r['case_id'] in case_ids if r['case_id'] else r['conversation_id'] in visible_conversations)]
    summary, rows = calculate(cases, attempts, events, feedback, start,end,as_of)
    summary['window'] = dict(start=start,end=end,as_of=as_of,data_origin=data_origin,scope='全部服务记录（模拟）' if u['role']=='admin' else u['group_name'])
    return summary, rows


@router.get('/api/analytics/summary')
def summary(start:str|None=None,end:str|None=None,data_origin:str='demo',u=Depends(user)):
    with db.connect() as conn:
        return collect(conn,u,start,end,data_origin)[0]


@router.get('/api/analytics/details')
def details(metric:str='first_submission_completeness',start:str|None=None,end:str|None=None,data_origin:str='demo',u=Depends(user)):
    with db.connect() as conn:
        summary, rows = collect(conn,u,start,end,data_origin)
        if metric not in rows: db.error('unknown_metric','未知指标。',404)
        return {'metric':metric,'rows':rows[metric],'total':len(rows[metric]),'window':summary['window']}


def safe_csv(value):
    text = str(value if value is not None else '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text


@router.get('/api/analytics/export')
def export(metric:str='first_submission_completeness',start:str|None=None,end:str|None=None,data_origin:str='demo',u=Depends(user)):
    result = details(metric,start,end,data_origin,u)
    output = StringIO()
    fields = sorted({k for r in result['rows'] for k in r})
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in result['rows']: writer.writerow([safe_csv(row.get(k)) for k in fields])
    return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="onboardflow-metric.csv"'})


class FeedbackInput(BaseModel):
    case_id: str|None = None
    conversation_id: str|None = None
    ease: int = Field(ge=1,le=5)
    outcome: Literal['resolved','unresolved','uncertain']
    @model_validator(mode='after')
    def one_subject(self):
        if bool(self.case_id) == bool(self.conversation_id): raise ValueError('只填写一个反馈对象')
        return self


@router.post('/api/feedback')
def submit_feedback(payload:FeedbackInput,u=Depends(user)):
    if u['role']!='employee': db.error('forbidden','请使用员工身份提交反馈。',403)
    with db.tx() as conn:
        if payload.case_id:
            case = get_case(conn,payload.case_id,u)
            if case['status'] not in ('resolved','closed'): db.error('invalid_transition','请求处理后再评价。',409)
            origin = case['data_origin']
        else:
            matching = [db.unpack(r) for r in conn.execute("SELECT * FROM events WHERE actor_id=? AND kind='answer_shown'",(u['id'],))]
            matching = [e for e in matching if e['metadata'].get('conversation_id')==payload.conversation_id]
            if not matching: db.error('forbidden','该咨询不存在或不属于你。',403)
            origin = matching[0]['data_origin']
        values = {'id':db.uid('feedback'),'case_id':payload.case_id,'conversation_id':payload.conversation_id,'user_id':u['id'],'ease':payload.ease,'outcome':payload.outcome,'created_at':db.now(),'data_origin':origin}
        try:
            conn.execute('INSERT INTO feedback(id,case_id,conversation_id,user_id,ease,outcome,created_at,data_origin) VALUES (:id,:case_id,:conversation_id,:user_id,:ease,:outcome,:created_at,:data_origin)',values)
        except sqlite3.IntegrityError:
            db.error('duplicate_feedback','此请求或咨询已提交反馈。',409)
        db.log(conn,'feedback_submitted',u['id'],payload.case_id,metadata={'feedback_id':values['id'],'conversation_id':payload.conversation_id},data_origin=origin)
        return values
