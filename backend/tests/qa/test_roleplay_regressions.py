"""Regressions from browser role enactment, independent persisted-state oracles."""
import pytest
from backend.tests.qa.test_security import client, login, create, act, persisted


def ready_for_result(c, category):
    case = create(c, category)
    login(c, 'hr01')
    case = act(c, case, 'accept').json()
    if category == 'ACCESS_DEVICE':
        case = act(c, case, 'create_task', assignee_id='it01').json()
        task = case['tasks'][0]
        login(c, 'it01')
        response = c.post(f"/api/tasks/{task['id']}/actions/complete", json={'row_version': task['row_version']})
        assert response.status_code == 200, response.text
        login(c, 'hr01')
        case = c.get(f"/api/cases/{case['id']}").json()
    return case


def test_progress_question_does_not_satisfy_hr_request_for_information(client):
    c = client
    case = create(c, 'MATERIAL_EXCEPTION')
    login(c, 'hr01')
    case = act(c, case, 'accept').json()
    case = act(c, case, 'ask_employee', body='请说明缺项原因及预计补交安排').json()
    deadline, first_response = case['due_at'], case['first_human_response_at']
    login(c)
    response = c.post(f"/api/cases/{case['id']}/messages", json={
        'row_version': case['row_version'], 'kind': 'progress_inquiry',
        'body': '进度如何？我还没拿到补充信息。',
    })
    assert response.status_code == 200, response.text
    case = response.json()
    assert case['status'] == 'waiting_employee'
    assert case['materials'][0]['provided'] is False
    assert case['due_at'] == deadline and case['first_human_response_at'] == first_response
    assert len([e for e in case['events'] if e['kind'] == 'progress_inquiry']) == 1
    assert case['events'][-1]['kind'] != 'status_changed'
    response = c.post(f"/api/cases/{case['id']}/messages", json={
        'row_version': case['row_version'], 'body': '学校确认12月20日提供，申请延期至该日。',
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'in_progress'
    assert response.json()['materials'][0]['provided'] is False


@pytest.mark.parametrize('category', ['GENERAL_ONBOARDING', 'ACCESS_DEVICE'])
@pytest.mark.parametrize('incorrect_code', ['approved_deferred', 'rejected'])
def test_material_decision_cannot_be_saved_for_other_services(client, category, incorrect_code):
    case = ready_for_result(client, category)
    before = persisted(case['id'])
    response = act(client, case, 'resolve', body='已核对完成，请员工确认', resolution_code=incorrect_code)
    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'invalid_resolution'
    assert persisted(case['id']) == before


@pytest.mark.parametrize('category', ['GENERAL_ONBOARDING', 'ACCESS_DEVICE'])
def test_non_material_result_is_answered_then_employee_confirms(client, category):
    case = ready_for_result(client, category)
    response = act(client, case, 'resolve', body='已按制度核对并完成必要任务，请确认演示结果。')
    assert response.status_code == 200, response.text
    case = response.json()
    assert case['resolution_code'] == 'answered' and case['status'] == 'resolved'
    assert case['materials'] == []
    login(client)
    assert act(client, case, 'confirm').json()['status'] == 'closed'


def test_it_receives_explicit_task_instruction_without_hr_notes(client):
    c = client
    case = create(c, 'ACCESS_DEVICE')
    login(c, 'hr01')
    case = act(c, case, 'accept').json()
    case = c.post(f"/api/cases/{case['id']}/messages", json={
        'row_version': case['row_version'], 'body': 'PRIVATE_HR_TRAINING_ARRANGEMENT', 'visibility': 'internal',
    }).json()
    instruction = '准备 Windows 演示电脑；不创建真实企业账号。'
    response = act(c, case, 'create_task', task_type='IT_DEVICE', assignee_id='it02',
                   body='HR_UNSENT_DRAFT', task_instruction=instruction)
    assert response.status_code == 200, response.text
    task = response.json()['tasks'][0]
    login(c, 'it02')
    result = c.get('/api/tasks')
    visible = next(t for t in result.json() if t['id'] == task['id'])
    assert visible['minimal_context']['instruction'] == instruction
    assert set(visible['minimal_context']) == {'id', 'name', 'city', 'planned_start_date', 'instruction'}
    assert 'PRIVATE_HR_TRAINING_ARRANGEMENT' not in result.text and 'HR_UNSENT_DRAFT' not in result.text
    assert c.get(f"/api/cases/{case['id']}").status_code == 404
    login(c, 'it01')
    assert all(t['id'] != task['id'] for t in c.get('/api/tasks').json())
