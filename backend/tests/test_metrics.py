from backend.app.analytics import calculate, safe_csv

START='2026-11-01T00:00:00+00:00'
END='2026-12-01T00:00:00+00:00'
NOW='2026-11-24T00:00:00+00:00'


def case(i,status,created='2026-11-01T00:00:00+00:00',due='2026-11-03T00:00:00+00:00',resolved=None,first=None,reopen=0):
    return dict(id=i,status=status,created_at=created,due_at=due,latest_resolved_at=resolved,
                first_human_response_at=first,reopen_count=reopen)


def attempt(i,draft,no,result,missing=None):
    return dict(id=i,draft_id=draft,attempt_no=no,attempted_at='2026-11-02T00:00:00+00:00',
                validation_result=result,missing_field_codes=missing or [],result_case_id=None)


def event(i,kind,metadata,case_id=None):
    return dict(id=i,kind=kind,metadata=metadata,case_id=case_id,occurred_at='2026-11-02T00:00:00+00:00')


def test_first_submission_includes_blocked_without_case_and_ignores_second_attempt():
    attempts=[attempt('a1','draft1',1,'invalid',['expected_date']),attempt('a2','draft1',2,'valid'),attempt('a3','draft2',1,'valid')]
    summary, rows=calculate([],attempts,[],[],START,END,NOW)
    metric=summary['metrics']['first_submission_completeness']
    assert (metric['numerator'],metric['denominator'],metric['value'])==(1,2,0.5)
    assert {r['id'] for r in rows['first_submission_completeness']}=={'a1','a3'}
    assert summary['breakdowns']['missing_fields']==[{'key':'expected_date','count':1}]


def test_sla_denominator_includes_open_late_reopened_but_excludes_cancelled():
    cases=[case('ontime','closed',resolved='2026-11-02T00:00:00+00:00'),
           case('late','resolved',resolved='2026-11-04T00:00:00+00:00'),
           case('open','waiting_employee'),case('reopened','in_progress',resolved='2026-11-02T00:00:00+00:00',reopen=1),
           case('cancelled','cancelled'),case('future','assigned',due='2026-11-25T00:00:00+00:00')]
    s,rows=calculate(cases,[],[],[],START,END,NOW)
    assert s['metrics']['on_time_resolution']['numerator']==1
    assert s['metrics']['on_time_resolution']['denominator']==4
    assert s['metrics']['on_time_resolution']['value']==0.25
    assert s['metrics']['resolution_hours']['value']==48 # (24 + 72)/2
    assert s['metrics']['cancelled']['value']==1
    assert s['metrics']['reopened']['value']==1
    assert len(rows['open_age_hours'])==3


def test_first_human_response_excludes_automatic_and_unanswered_is_not_zero():
    cases=[case('one','assigned',first='2026-11-01T03:00:00+00:00'),case('two','assigned')]
    s,rows=calculate(cases,[],[event('auto','case_submitted',{},'two'),event('note','internal_note',{},'two')],[],START,END,NOW)
    assert s['metrics']['first_response_hours']['value']==3
    assert s['metrics']['first_response_hours']['sample_size']==1
    assert s['metrics']['awaiting_first_response_hours']['value']==23*24
    assert rows['awaiting_first_response_hours'][0]['id']=='two'


def test_conversation_count_feedback_and_progress_are_different_units():
    events=[event('q1','question_submitted',{'conversation_id':'c1','category':'GENERAL_ONBOARDING'}),
            event('q2','question_submitted',{'conversation_id':'c1','category':'GENERAL_ONBOARDING'}),
            event('q3','question_submitted',{'conversation_id':'c2','category':'OTHER'}),
            event('a1','answer_shown',{'conversation_id':'c1','provider_mode':'offline','reason_code':'matched'}),
            event('p1','employee_message',{'kind':'progress_inquiry'},'case1'),
            event('p2','employee_message',{'kind':'supplement'},'case1')]
    cases=[case('case1','closed',resolved='2026-11-02T00:00:00+00:00')]
    feedback=[dict(id='f1',case_id=None,conversation_id='c1',ease=4,outcome='uncertain',created_at='2026-11-03T00:00:00+00:00')]
    s,_=calculate(cases,[],events,feedback,START,END,NOW)
    m=s['metrics']
    assert m['consultations']['value']==2 and m['question_messages']['value']==3
    assert (m['feedback_response']['numerator'],m['feedback_response']['denominator'])==(1,2)
    assert m['ease_score']['value']==4 and m['repeat_contacts']['value']==1


def test_empty_denominators_are_none_and_window_is_half_open():
    s,_=calculate([],[],[dict(event('q','question_submitted',{'conversation_id':'x'}),occurred_at=END)],[],START,END,NOW)
    assert s['metrics']['consultations']['value']==0
    assert s['metrics']['first_submission_completeness']['value'] is None
    assert s['metrics']['on_time_resolution']['value'] is None
    assert s['metrics']['ease_score']['value'] is None


def test_changing_source_record_recomputes_result():
    cases=[case('case1','in_progress')]
    before,_=calculate(cases,[],[],[],START,END,NOW)
    cases[0].update(status='resolved',latest_resolved_at='2026-11-02T00:00:00+00:00')
    after,_=calculate(cases,[],[],[],START,END,NOW)
    assert before['metrics']['on_time_resolution']['value']==0
    assert after['metrics']['on_time_resolution']['value']==1


def test_csv_formula_neutralization():
    for text in ['=SUM(1,2)',' +1','-2','@cmd','\t=1','\r+1']:
        assert safe_csv(text).startswith("'")
    assert safe_csv('广州')=='广州'


def test_analytics_api_group_scope_and_employee_denial(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.tests.test_cases import login
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'analytics.sqlite3'))
    with TestClient(app) as c:
        login(c,'hr01')
        result=c.get('/api/analytics/summary?start=2026-11-01').json()
        assert result['window']['scope']=='广州HR'
        rows=c.get('/api/analytics/details?metric=on_time_resolution&start=2026-11-01').json()['rows']
        assert rows and all(r['assigned_group']=='广州HR' for r in rows)
        admin_group=result['metrics']['first_submission_completeness']['denominator']
        login(c,'admin01')
        assert c.get('/api/analytics/summary?start=2026-11-01').json()['metrics']['first_submission_completeness']['denominator']>admin_group
        login(c,'emp01')
        for url in ('/api/analytics/summary','/api/analytics/details','/api/analytics/export'):
            assert c.get(url).status_code==403


def test_feedback_ownership_duplicate_and_response_rate(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.tests.test_cases import login
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'feedback.sqlite3'))
    monkeypatch.setenv('DATA_ORIGIN','manual')
    with TestClient(app) as c:
        login(c,'emp01')
        query=c.post('/api/assistant/query',json={'question':'入职材料清单有哪些？'}).json()
        payload=dict(conversation_id=query['conversation_id'],ease=4,outcome='uncertain')
        login(c,'emp02')
        assert c.post('/api/feedback',json=payload).status_code==403
        login(c,'emp01')
        assert c.post('/api/feedback',json=payload).status_code==200
        assert c.post('/api/feedback',json=payload).status_code==409
        login(c,'admin01')
        summary=c.get('/api/analytics/summary?data_origin=manual').json()
        assert summary['metrics']['feedback_response']['numerator']==1
        assert summary['metrics']['feedback_response']['denominator']==1
        assert summary['breakdowns']['feedback_outcomes']==[{'key':'uncertain','count':1}]


def test_database_record_change_updates_dashboard(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import db
    from backend.tests.test_cases import login
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'recompute.sqlite3'))
    with TestClient(app) as c:
        login(c,'admin01')
        path='/api/analytics/summary?start=2026-10-01'
        before=c.get(path).json()
        # Isolated fixture changes one source row, never a dashboard cache/counter.
        with db.tx() as conn:
            row=conn.execute("SELECT id,created_at FROM cases WHERE data_origin='demo' AND status='assigned' LIMIT 1").fetchone()
            conn.execute("UPDATE cases SET status='resolved',latest_resolved_at=? WHERE id=?",(row['created_at'],row['id']))
        after=c.get(path).json()
        assert after['metrics']['on_time_resolution']['numerator']==before['metrics']['on_time_resolution']['numerator']+1
        assert after['metrics']['on_time_resolution']['denominator']==before['metrics']['on_time_resolution']['denominator']
        assert after['metrics']['open_age_hours']['sample_size']==before['metrics']['open_age_hours']['sample_size']-1
        assert after['breakdowns']['missing_items']


def test_blocked_first_submission_is_visible_in_live_metrics(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.tests.test_cases import login,draft,submit
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'attempt_metric.sqlite3'))
    monkeypatch.setenv('DATA_ORIGIN','manual')
    with TestClient(app) as c:
        login(c)
        d=draft(c,fields={'description':'信息表未准备好'})
        assert submit(c,d).status_code==422
        assert submit(c,d).status_code==422
        login(c,'admin01')
        s=c.get('/api/analytics/summary?data_origin=manual').json()
        m=s['metrics']['first_submission_completeness']
        assert m['numerator']==0 and m['denominator']==1
        for key in ('categories','handoff_reasons','missing_fields','statuses','provider_modes','missing_items'):
            assert c.get(f'/api/analytics/details?data_origin=manual&metric={key}').status_code==200
        login(c)
        d=draft(c,draft_id=d['id'],draft_version=d['draft_version'])
        assert submit(c,d,key='metrics-active-new-attempt').status_code==200
        login(c,'admin01')
        m=c.get('/api/analytics/summary?data_origin=manual').json()['metrics']['first_submission_completeness']
        assert m['numerator']==0 and m['denominator']==1
