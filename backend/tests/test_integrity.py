"""Independent reviewer tests for multi-command races and historical integrity."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from backend.app import db, auth
from backend.app.main import app

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'integrity.sqlite3'))
    monkeypatch.setattr(db,'_clock',lambda:datetime(2026,11,24,8,tzinfo=timezone.utc))
    with TestClient(app) as c:
        login(c)
        yield c

def login(c,id='emp01'):
    r=c.post('/api/demo/login',json={'user_id':id});assert r.status_code==200,r.text
    c.headers['X-CSRF-Token']=r.json()['csrf_token']
    return r.json()

def make_draft(c,category='MATERIAL_EXCEPTION',fields=None):
    r=c.post('/api/cases/draft',json={'category':category,'fields':fields or {'description':'申请延期','missing_item':'入职信息表','expected_date':'2026-12-05'}})
    assert r.status_code==200,r.text
    return r.json()

def submit(c,d,key='integrity-key'):
    r=c.post('/api/cases',headers={'Idempotency-Key':key},json={'draft_id':d['id'],'draft_version':d['draft_version'],'confirmation_token':d['confirmation_token'],'confirmed':True,'allow_duplicate':True})
    assert r.status_code==200,r.text
    return r.json()

def test_two_concurrent_commands_one_commits_other_conflicts(client):
    case=submit(client,make_draft(client));session=login(client,'hr01')
    barrier=Barrier(2)
    def worker():
        c=TestClient(app);c.cookies.update(client.cookies);c.headers['X-CSRF-Token']=session['csrf_token']
        barrier.wait()
        r=c.post(f"/api/cases/{case['id']}/actions/accept",json={'row_version':case['row_version']})
        c.close()
        return r.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _:worker(),range(2)))==[200,409]
    detail=client.get('/api/cases/'+case['id']).json()
    assert detail['row_version']==case['row_version']+1
    events=[e for e in detail['events'] if e['kind']=='status_changed' and e['metadata']['to_status']=='in_progress']
    assert len(events)==1

def test_two_concurrent_confirmations_create_single_case_and_attempt(client):
    d=make_draft(client);cookies=dict(client.cookies);csrf=client.headers['X-CSRF-Token'];barrier=Barrier(2)
    def worker():
        c=TestClient(app);c.cookies.update(cookies);c.headers['X-CSRF-Token']=csrf
        barrier.wait()
        result=submit(c,d,'race-creation-key');c.close();return result['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(lambda _:worker(),range(2)))
    assert ids[0]==ids[1]
    with db.tx() as c:
        assert c.execute('SELECT COUNT(*) FROM submission_attempts WHERE draft_id=?',(d['id'],)).fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM cases WHERE id=?',(ids[0],)).fetchone()[0]==1

def test_draft_edit_requires_current_version(client):
    d=make_draft(client)
    body={'category':d['category'],'fields':d['fields'],'draft_id':d['id']}
    assert client.post('/api/cases/draft',json=body).status_code==409
    body['draft_version']=d['draft_version']
    updated=client.post('/api/cases/draft',json=body)
    assert updated.status_code==200
    assert client.post('/api/cases/draft',json=body).status_code==409

def test_planned_date_change_refreshes_policy_not_case_basis(client):
    case=submit(client,make_draft(client,'GENERAL_ONBOARDING',{'description':'报到地点'}))
    refs=case['policy_snapshot_refs'];due=case['due_at']
    login(client,'hr01')
    r=client.post('/api/employees/emp01/profile',json={'planned_start_date':'2026-10-20','expected_planned_start_date':'2026-12-01','reason':'模拟资料更正'})
    assert r.status_code==200
    login(client)
    ca=client.get('/api/cases/'+case['id']).json()
    assert ca['profile_changed'] and ca['policy_snapshot_refs']==refs and ca['due_at']==due
    answer=client.post('/api/assistant/query',json={'question':'广州报到地点与联系人'}).json()
    assert any(ref['article_version_id']=='pol-report-gz-v1' for ref in answer['citation_refs'])
    assert client.get('/api/knowledge/pol-report-gz-v2').status_code==200

def test_self_reported_date_change_also_flags_old_case(client):
    login(client,'emp07')
    assert client.post('/api/assistant/context',json={'city':'广州','planned_start_date':'2026-12-01'}).status_code==200
    case=submit(client,make_draft(client))
    assert client.post('/api/assistant/context',json={'planned_start_date':'2027-01-01'}).status_code==200
    ca=client.get('/api/cases/'+case['id']).json()
    assert ca['profile_changed']
    assert ca['context_source']['planned_start_date']=='2026-12-01'
    assert ca['context_source']['source']['planned_start_date']=='self_reported_context'

def test_metadata_supply_updates_checklist_without_real_upload(client):
    ca=submit(client,make_draft(client))
    assert ca['materials'][0]['required_item_code']=='onboarding_form'
    assert client.post('/api/cases/'+ca['id']+'/materials',json={'row_version':ca['row_version'],'required_item_code':'入职信息表','provided':True}).status_code==200
    items=client.get('/api/checklist').json()['items']
    assert next(i for i in items if i['code']=='onboarding_form')['provided'] is True
    assert next(i for i in items if i['code']=='employment_information')['provided'] is False

def test_task_company_boundary_for_legacy_imported_rows(client):
    # Isolated deliberately inconsistent import; normal task creation prevents this.
    with db.tx() as c:
        c.execute("UPDATE users SET company='other-company' WHERE id='emp02'")
        row=c.execute("SELECT t.id FROM tasks t JOIN cases c ON c.id=t.case_id WHERE c.requester_id='emp02' LIMIT 1").fetchone()
        assert row
        tid=row['id'];c.execute("UPDATE tasks SET assignee_id='it01' WHERE id=?",(tid,))
    login(client,'it01')
    assert all(t['id']!=tid for t in client.get('/api/tasks').json())
    assert client.post('/api/tasks/'+tid+'/actions/complete',json={'row_version':1}).status_code==404
    login(client,'admin01')
    assert all(t['id']!=tid for t in client.get('/api/tasks').json())

def test_demo_login_rejects_remote_network_peer():
    request=Request({'type':'http','method':'POST','path':'/api/demo/login','headers':[], 'client':('203.0.113.5',1234)})
    with pytest.raises(HTTPException) as error:
        auth.demo_allowed(request)
    assert error.value.status_code==403

@pytest.mark.parametrize('decision,reason',[('approved_deferred','本次演示延期理由'),('rejected','本次申请条件不适用')])
def test_material_decision_preserves_explicit_reason(client,decision,reason):
    ca=submit(client,make_draft(client));login(client,'hr01')
    ca=client.post('/api/cases/'+ca['id']+'/actions/accept',json={'row_version':ca['row_version']}).json()
    r=client.post('/api/cases/'+ca['id']+'/actions/resolve',json={'row_version':ca['row_version'],'body':'请查看本次申请的处理结果，材料仍待提交。','reason':reason,'resolution_code':decision,'expected_date':'2026-12-05'})
    assert r.status_code==200,r.text
    assert r.json()['structured_fields']['approval_reason']==reason
    assert r.json()['materials'][0]['provided'] is False
