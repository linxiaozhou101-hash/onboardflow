"""Independent qualification: new boundaries, invalid input, metamorphic scope checks,
and randomized independent metric oracles. Never accesses the demo database."""
import copy
import itertools
import json
import random
from datetime import datetime,timedelta,timezone
import pytest
from fastapi.testclient import TestClient
from backend.app import db,knowledge,analytics
from backend.app.main import app

NOW=datetime(2026,11,24,13,tzinfo=timezone.utc)
@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'qa-policy.sqlite3'))
    monkeypatch.setenv('DATA_ORIGIN','test')
    monkeypatch.setenv('LLM_MODE','offline')
    monkeypatch.setattr(db,'_clock',lambda:NOW)
    with TestClient(app,raise_server_exceptions=False) as c:
        yield c

def login(c,id='emp01'):
    r=c.post('/api/demo/login',json={'user_id':id}); assert r.status_code==200,r.text
    c.headers['X-CSRF-Token']=r.json()['csrf_token'];return r.json()['user']

def policy_body(**updates):
    return {'policy_key':'qa-new-policy','version':'qa-1','title':'新员工首周包裹领取细则','category':'GENERAL_ONBOARDING','cities':['广州'],'employment_types':['正式员工'],'effective_from':'2026-11-01','content_markdown':'## 首日安排\n首日请向服务台签到。\n\n## 包裹领取\n首周包裹统一在紫藤收发室领取，开放时段为下午 14:00。',**updates}

def publish(c,payload):
    login(c,'hr01');r=c.post('/api/admin/knowledge/drafts',json=payload);assert r.status_code==200,r.text
    p=r.json();login(c,'admin01');r=c.post('/api/admin/knowledge/'+p['id']+'/publish',json={'row_version':1});assert r.status_code==200,r.text
    return r.json()

def test_retrieval_cites_relevant_nonfirst_clause(client):
    p=publish(client,policy_body());login(client)
    r=client.post('/api/assistant/query',json={'question':'首周包裹要去哪里领取，开放时间是什么？'});assert r.status_code==200,r.text
    result=r.json();assert result['action']=='answer',result
    assert '紫藤收发室' in result['answer_text'],result
    assert '14:00' in result['answer_text']
    citation=next(ref for ref in result['citation_refs'] if ref['article_version_id']==p['id'])
    assert citation['section_id']=='section-2'
    assert '紫藤收发室' in citation['excerpt']

@pytest.mark.parametrize('items',[[{'label':'没有材料编码'}],[{'code':'x'}],['不是条目对象'],{'code':'x','label':'错误容器'},[{'code':'x','label':7}],[{'code':'','label':'空编码'}],[{'code':'x','label':'甲'},{'code':'x','label':'乙'}]])
def test_malformed_structured_material_rules_rejected_without_500(client,items):
    login(client,'hr01')
    response=client.post('/api/admin/knowledge/drafts',json=policy_body(rules={'required_items':items}))
    assert response.status_code==422,response.text
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM policies WHERE policy_key='qa-new-policy'").fetchone()[0]==0

@pytest.mark.parametrize('replacement',['no-such-policy'])
def test_invalid_replacement_draft_is_client_error_not_database_crash(client,replacement):
    login(client,'hr01')
    response=client.post('/api/admin/knowledge/drafts',json=policy_body(replaces_id=replacement))
    assert response.status_code in (400,409,422),response.text


def test_unrelated_replacement_draft_may_save_but_cannot_publish(client):
    login(client,'hr01')
    response=client.post('/api/admin/knowledge/drafts',json=policy_body(replaces_id='pol-hr-internal'))
    assert response.status_code==200
    login(client,'admin01')
    response=client.post('/api/admin/knowledge/'+response.json()['id']+'/publish',json={'row_version':1})
    assert response.status_code==409


def test_history_rechecks_current_scope_after_self_report_change(client):
    login(client,'emp07')
    assert client.post('/api/assistant/context',json={'city':'广州','planned_start_date':'2026-12-01'}).status_code==200
    answer=client.post('/api/assistant/query',json={'question':'报到地点在哪里？'}).json()
    assert any(ref['article_version_id']=='pol-report-gz-v2' for ref in answer['citation_refs'])
    assert client.post('/api/assistant/context',json={'city':'深圳'}).status_code==200
    assert client.get('/api/knowledge/pol-report-gz-v2').status_code==403
    history=client.get('/api/assistant/history')
    assert history.status_code==200
    assert '广州演示园区' not in history.text, 'Revoked city scope still returns full historical excerpt'


def test_published_revision_immutable_for_all_write_routes(client):
    p=publish(client,policy_body());login(client,'hr01')
    before=client.get('/api/knowledge/'+p['id']).json()
    r=client.put('/api/admin/knowledge/'+p['id'],json=policy_body(content_markdown='## 改写\n未经审核直接覆盖旧内容',row_version=2))
    assert r.status_code==409
    after=client.get('/api/knowledge/'+p['id']).json()
    assert before['content_hash']==after['content_hash'] and before['content_markdown']==after['content_markdown']


def test_scope_intersection_matches_exhaustive_set_oracle():
    """2000 deterministic combinations. Oracle enumerates individual people+days."""
    rng=random.Random(407)
    city_choices=[['广州'],['深圳'],['广州','深圳'],['*'],['全部']]
    type_choices=[['正式员工'],['实习生'],['正式员工','实习生'],['*'],['全部']]
    days=['2026-11-01','2026-11-02','2026-11-03','2026-11-04']
    def point_set(p):
        cities=['广州','深圳'] if any(x in ('*','全部') for x in p['cities']) else p['cities']
        kinds=['正式员工','实习生'] if any(x in ('*','全部') for x in p['employment_types']) else p['employment_types']
        return {(city,kind,day) for city,kind,day in itertools.product(cities,kinds,days) if day>=p['effective_from'] and (not p['effective_to'] or day<p['effective_to'])}
    def one():
        start=rng.randrange(3);end=rng.choice([None,*range(start+1,4)])
        return {'cities':rng.choice(city_choices),'employment_types':rng.choice(type_choices),'effective_from':days[start],'effective_to':None if end is None else days[end]}
    for index in range(2000):
        a,b=one(),one();expected=bool(point_set(a)&point_set(b))
        assert knowledge.overlap(a,b)==expected,(index,a,b)
        assert knowledge.overlap(a,b)==knowledge.overlap(b,a),(index,a,b)


def test_effective_boundary_every_scope_uses_planned_date(client):
    login(client)
    with db.tx() as conn:
        for city,kind in itertools.product(['广州','深圳'],['正式员工','实习生']):
            person=db.unpack(conn.execute("SELECT * FROM users WHERE id='emp01'").fetchone())
            person.update(city=city,employment_type=kind)
            for day in ['2026-10-31','2026-11-01','2026-12-31','2027-01-01']:
                found=knowledge.applicable(conn,person,{'city':city,'employment_type':kind,'planned_start_date':day})
                for p in found:
                    assert p['effective_from']<=day and (p['effective_to'] is None or day<p['effective_to'])
                    assert city in (['广州','深圳'] if '*' in p['cities'] else p['cities'])
                    assert kind in (['正式员工','实习生'] if '*' in p['employment_types'] else p['employment_types'])
                report=next(p for p in found if p['policy_key']==('report-gz' if city=='广州' else 'report-sz'))
                assert report['version']==('1.0' if day<'2026-11-01' else '2.0') if city=='广州' else report['version']==('1.0' if day<'2027-01-01' else '2.0')


def test_metrics_randomized_against_independent_scalar_oracle():
    rng=random.Random(103)
    start=datetime(2026,11,1,tzinfo=timezone.utc);end=NOW
    cases=[];attempts=[]
    for i in range(350):
        born=start+timedelta(hours=rng.randrange(-60,500));due=born+timedelta(hours=rng.choice([24,48]));status=rng.choice(['assigned','in_progress','waiting_employee','resolved','closed','cancelled'])
        resolved=born+timedelta(hours=rng.randrange(1,96)) if status in ('resolved','closed') else None
        # Use physically possible current rows; only as-of history is being measured.
        if resolved and resolved>NOW:status='in_progress';resolved=None
        cases.append({'id':str(i),'status':status,'created_at':born.isoformat(),'due_at':due.isoformat(),'latest_resolved_at':resolved.isoformat() if resolved else None,'first_human_response_at':None,'reopen_count':0})
        attempts.append({'id':'a'+str(i),'draft_id':'d'+str(i),'attempt_no':1,'attempted_at':born.isoformat(),'validation_result':rng.choice(['valid','invalid']),'missing_field_codes':['description']})
        attempts.append({**attempts[-1],'id':'b'+str(i),'attempt_no':2,'validation_result':'valid'})
    expected_due=0;expected_success=0;expected_first=0;expected_complete=0
    for c in cases:
        due=datetime.fromisoformat(c['due_at'])
        if c['status']!='cancelled' and start<=due<end:
            expected_due+=1
            if c['status'] in ('resolved','closed') and datetime.fromisoformat(c['latest_resolved_at'])<=due:expected_success+=1
    for a in attempts:
        if a['attempt_no']==1 and start<=datetime.fromisoformat(a['attempted_at'])<end:
            expected_first+=1;expected_complete+=a['validation_result']=='valid'
    rng.shuffle(cases);rng.shuffle(attempts)
    summary,rows=analytics.calculate(cases,attempts,[],[],start.isoformat(),end.isoformat(),NOW.isoformat())
    m=summary['metrics']
    assert (m['on_time_resolution']['numerator'],m['on_time_resolution']['denominator'])==(expected_success,expected_due)
    assert (m['first_submission_completeness']['numerator'],m['first_submission_completeness']['denominator'])==(expected_complete,expected_first)
    shuffled,_=analytics.calculate(list(reversed(cases)),list(reversed(attempts)),[],[],start.isoformat(),end.isoformat(),NOW.isoformat())
    assert shuffled['metrics']==summary['metrics']


def test_supplement_followup_never_changes_application_feedback_denominator(client):
    # Legal seed case demo_case_005 is closed. Material/feedback state is independent.
    login(client,'admin01')
    before=client.get('/api/analytics/summary?data_origin=demo').json()
    with db.tx() as conn:
        conn.execute("UPDATE materials SET provided=1,format_check_status='metadata_valid' WHERE case_id IN (SELECT id FROM cases WHERE status='closed')")
    after=client.get('/api/analytics/summary?data_origin=demo').json()
    assert before['metrics']['feedback_response']==after['metrics']['feedback_response']
    assert before['metrics']['on_time_resolution']==after['metrics']['on_time_resolution']


def test_reopened_feedback_not_silently_removed_from_service_feedback():
    start='2026-11-01T00:00:00+00:00';end='2026-11-25T00:00:00+00:00'
    c={'id':'x','status':'closed','created_at':start,'due_at':'2026-11-03T00:00:00+00:00','latest_resolved_at':'2026-11-02T00:00:00+00:00','first_human_response_at':None,'reopen_count':0}
    f={'id':'f','case_id':'x','conversation_id':None,'ease':1,'outcome':'unresolved','created_at':'2026-11-02T01:00:00+00:00'}
    before,_=analytics.calculate([c],[],[],[f],start,end,NOW.isoformat())
    reopened={**c,'status':'in_progress','reopen_count':1}
    after,_=analytics.calculate([reopened],[],[],[f],start,end,NOW.isoformat())
    assert before['metrics']['feedback_response']['denominator']==1
    assert after['metrics']['feedback_response']['denominator']==1, 'reopening must not erase a real feedback opportunity or adverse feedback'
    assert after['metrics']['ease_score']['sample_size']==1
    assert after['breakdowns']['feedback_outcomes']==[{'key':'unresolved','count':1}]


def test_publication_timestamp_offset_semantics_not_lexicographic(client):
    # Import/legacy fixture for same instant represented with different timezone offset.
    login(client)
    with db.tx() as conn:
        conn.execute("UPDATE policies SET published_at=? WHERE id='pol-report-gz-v2'",('2026-11-24T20:59:59+08:00',))
    r=client.get('/api/knowledge/pol-report-gz-v2')
    assert r.status_code==200,r.text


def test_withdrawal_during_remote_wait_rechecks_authorized_fragments(client,monkeypatch):
    import threading
    import httpx
    from concurrent.futures import ThreadPoolExecutor
    from backend.app import providers
    p=publish(client,policy_body());login(client)
    monkeypatch.setenv('LLM_MODE','remote');monkeypatch.setenv('LLM_API_KEY','synthetic-test-key')
    monkeypatch.setenv('LLM_MODEL','synthetic');monkeypatch.setenv('LLM_ENDPOINT','https://example.invalid/chat/completions')
    entered=threading.Event();release=threading.Event()
    def wait_provider(*args,**kwargs):
        entered.set();assert release.wait(5)
        raise httpx.ReadTimeout('controlled timeout, no network')
    monkeypatch.setattr(providers.httpx,'post',wait_provider)
    with TestClient(app,raise_server_exceptions=False) as admin:
        login(admin,'admin01')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(client.post,'/api/assistant/query',json={'question':'包裹领取地点与时间？'})
            assert entered.wait(2)
            try:
                withdrawn=admin.post('/api/admin/knowledge/'+p['id']+'/withdraw',json={'row_version':2})
                assert withdrawn.status_code==200,withdrawn.text
            finally:
                release.set()
            response=future.result(timeout=5)
    assert response.status_code==200,response.text
    payload=response.json()
    assert payload['provider_failure']=='context_changed_during_generation'
    assert all(ref['article_version_id']!=p['id'] for ref in payload['citation_refs'])
    assert '紫藤收发室' not in payload['answer_text']
    assert payload['action']=='offer_handoff'


def test_self_reported_routing_uses_same_scope_for_cases_attempts_queries_and_feedback(client):
    login(client,'emp07')
    assert client.post('/api/assistant/context',json={'city':'深圳','planned_start_date':'2026-12-01'}).status_code==200
    query=client.post('/api/assistant/query',json={'question':'入职材料清单有哪些？'}).json()
    assert query['action']=='answer'
    assert client.post('/api/feedback',json={'conversation_id':query['conversation_id'],'ease':2,'outcome':'uncertain'}).status_code==200
    fields={'description':'自报深圳的材料补交','missing_item':'入职信息表'}
    d=client.post('/api/cases/draft',json={'category':'MATERIAL_EXCEPTION','fields':fields}).json()
    body={'draft_id':d['id'],'draft_version':d['draft_version'],'confirmation_token':d['confirmation_token'],'confirmed':True,'allow_duplicate':True}
    assert client.post('/api/cases',json=body,headers={'Idempotency-Key':'qa-scope-missing'}).status_code==422
    login(client,'hr02');before=client.get('/api/analytics/summary?data_origin=test').json()
    assert before['metrics']['first_submission_completeness']['denominator']==1
    assert before['metrics']['consultations']['value']==1
    assert before['metrics']['feedback_response']['numerator']==1
    login(client,'emp07');fields['expected_date']='2026-12-05'
    d=client.post('/api/cases/draft',json={'category':'MATERIAL_EXCEPTION','fields':fields,'draft_id':d['id'],'draft_version':1}).json()
    body.update(draft_version=d['draft_version'],confirmation_token=d['confirmation_token'])
    response=client.post('/api/cases',json=body,headers={'Idempotency-Key':'qa-scope-valid'});assert response.status_code==200,response.text
    ca=response.json();assert ca['assigned_group']=='深圳HR'
    login(client,'hr02')
    r=client.post('/api/cases/'+ca['id']+'/actions/accept',json={'row_version':ca['row_version']});assert r.status_code==200;r=r.json()
    r=client.post('/api/cases/'+ca['id']+'/actions/resolve',json={'row_version':r['row_version'],'resolution_code':'rejected','body':'本次安排不批准，已说明可重新申请。'});assert r.status_code==200,r.text
    login(client,'emp07');assert client.post('/api/feedback',json={'case_id':ca['id'],'ease':1,'outcome':'unresolved'}).status_code==200
    login(client,'hr02');after=client.get('/api/analytics/summary?data_origin=test').json()
    assert after['metrics']['first_submission_completeness']['denominator']==1
    assert after['metrics']['first_submission_completeness']['numerator']==0
    assert after['metrics']['feedback_response']['numerator']==2
    assert after['metrics']['feedback_response']['denominator']==2
    login(client,'hr01');other=client.get('/api/analytics/summary?data_origin=test').json()
    assert other['metrics']['first_submission_completeness']['denominator']==0
    assert other['metrics']['consultations']['value']==0
    assert other['metrics']['feedback_response']['numerator']==0


def test_analytics_explicit_shanghai_calendar_window_honors_offset(client):
    with db.tx() as conn:
        db.log(conn,'question_submitted','emp01',metadata={'conversation_id':'local-23'},data_origin='test',occurred_at='2026-11-23T15:59:59+00:00')
        db.log(conn,'question_submitted','emp01',metadata={'conversation_id':'local-24'},data_origin='test',occurred_at='2026-11-23T16:00:00+00:00')
    login(client,'admin01')
    response=client.get('/api/analytics/summary',params={'data_origin':'test','start':'2026-11-24T00:00:00+08:00','end':'2026-11-25T00:00:00+08:00'})
    assert response.status_code==200,response.text
    assert response.json()['metrics']['consultations']['value']==1
    details=client.get('/api/analytics/details',params={'data_origin':'test','start':'2026-11-24T00:00:00+08:00','end':'2026-11-25T00:00:00+08:00','metric':'consultations'}).json()
    assert details['rows'][0]['metadata']['conversation_id']=='local-24'
