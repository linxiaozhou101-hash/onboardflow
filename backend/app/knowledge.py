"""Permission-first Chinese retrieval and immutable, effective-dated policy versions."""
from __future__ import annotations
import hashlib
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import db
from .auth import user, require_hr

router = APIRouter()
CITIES = {'广州', '深圳'}
TYPES = {'正式员工', '实习生'}
ROLES = {'employee', 'hr', 'admin', 'it'}
CONTEXT_FIELDS = ('city', 'employment_type', 'planned_start_date')
DISCLAIMER = '仅用于作品演示，不代表任何真实企业制度。'
RULES = {
    'MATERIAL_EXCEPTION': {'required_fields': ['description', 'missing_item', 'expected_date'], 'hr_approval_required': True, 'completion_codes': ['approved_deferred', 'rejected']},
    'ACCESS_DEVICE': {'required_fields': ['description', 'device_type'], 'required_it_task': True},
    'GENERAL_ONBOARDING': {'required_fields': ['description'], 'citation_required': True},
}
RETRIEVAL_CONFIG = {'ngram_range': [1, 2, 3], 'threshold': 0.10, 'top_k': 3, 'calibration': '公开开发集自动筛查；相似度不是正确概率'}


def fail(code, message, status=400):
    return db.error(code, message, status)


def decode(row):
    if row is None:
        return None
    out = dict(row)
    for key in ('cities', 'employment_types', 'visibility_roles', 'rules'):
        if isinstance(out.get(key), str):
            out[key] = json.loads(out[key])
    out['policy_id'] = out['id']
    return out


def init_knowledge(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS policies (
      id TEXT PRIMARY KEY, policy_key TEXT NOT NULL, version TEXT NOT NULL,
      title TEXT NOT NULL, category TEXT NOT NULL, scope_company TEXT NOT NULL,
      cities TEXT NOT NULL, employment_types TEXT NOT NULL, visibility_roles TEXT NOT NULL,
      status TEXT NOT NULL, published_at TEXT, effective_from TEXT NOT NULL, effective_to TEXT,
      owner_id TEXT NOT NULL, approved_by TEXT, content_markdown TEXT NOT NULL,
      content_hash TEXT NOT NULL, rules TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      replaces_id TEXT REFERENCES policies(id), row_version INTEGER NOT NULL DEFAULT 1,
      UNIQUE(scope_company,policy_key,version)
    );
    CREATE TABLE IF NOT EXISTS knowledge_answers (
      id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
      question TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_answers_user ON knowledge_answers(user_id,conversation_id);
    ''')


def expanded(values, universe):
    return set(universe) if '*' in values or '全部' in values else set(values)


def scope_match(p, u, ctx):
    return (p['scope_company'] == u['company'] and u['role'] in p['visibility_roles']
            and ctx.get('city') in expanded(p['cities'], CITIES)
            and ctx.get('employment_type') in expanded(p['employment_types'], TYPES))


def resolve_context(conn, u):
    record = conn.execute('SELECT context FROM query_context WHERE user_id=?', (u['id'],)).fetchone()
    reported = json.loads(record['context']) if record else {}
    context = {key: u.get(key) or reported.get(key) for key in CONTEXT_FIELDS}
    context['source'] = {key: 'verified_profile' if u.get(key) else 'self_reported_context' if reported.get(key) else 'missing' for key in CONTEXT_FIELDS}
    return context


def publication_available(policy):
    """Compare instants, including legacy ISO timestamps with explicit offsets."""
    if not policy.get('published_at'):
        return False
    try:
        published = datetime.fromisoformat(policy['published_at'].replace('Z', '+00:00'))
        if published.tzinfo is None:
            return False
        return published.astimezone(timezone.utc) <= datetime.fromisoformat(db.now())
    except (TypeError, ValueError):
        return False


def applicable(conn, u, context=None):
    ctx = context or resolve_context(conn, u)
    if any(not ctx.get(k) for k in CONTEXT_FIELDS):
        return []
    planned = ctx['planned_start_date']
    return [p for row in conn.execute("SELECT * FROM policies WHERE status='published'")
            if scope_match(p := decode(row), u, ctx)
            and publication_available(p)
            and p['effective_from'] <= planned
            and (not p['effective_to'] or planned < p['effective_to'])]


def conflicts(policies):
    seen = set()
    for policy in policies:
        if policy['policy_key'] in seen:
            return True
        seen.add(policy['policy_key'])
    return False


def sections(p):
    # Stable section anchors derived from headings, not model-generated identifiers.
    chunks = re.split(r'(?m)^## ', p['content_markdown'])
    return [{'section_id': f'section-{i}', 'excerpt': chunk.strip()} for i, chunk in enumerate(chunks) if chunk.strip() and i > 0]


def citation(p, section=None):
    sec = section or (sections(p) or [{'section_id': 'section-0', 'excerpt': p['content_markdown']}])[0]
    return {key: p[key] for key in ('title', 'version', 'effective_from', 'effective_to', 'cities', 'employment_types')} | {
        'article_version_id': p['id'], 'section_id': sec['section_id'], 'excerpt': sec['excerpt'],
        'url': f"/api/knowledge/{p['id']}#{sec['section_id']}", 'content_hash': p['content_hash']}


def citations_for(conn, u, category):
    policies = applicable(conn, u)
    if conflicts(policies):
        return []
    chosen = [p for p in policies if p['category'] == category]
    return [citation(p) for p in chosen[:3]]


def grams(text):
    text = re.sub(r'\s+', '', text.lower())
    return Counter(text[i:i+n] for n in range(1, 4) for i in range(max(0, len(text)-n+1)))


def rank(query, policies):
    """Reproducible local char 1–3 gram TF-IDF, cosine similarity; no cache."""
    documents = [grams(p['title'] + '\n' + p['content_markdown']) for p in policies]
    q = grams(query)
    document_frequency = Counter(term for d in documents for term in d)
    idf = {term: math.log((1+len(documents))/(1+count))+1 for term, count in document_frequency.items()}
    def vector(counts):
        return {t: (1+math.log(n))*idf[t] for t, n in counts.items() if t in idf}
    qv = vector(q)
    qnorm = math.sqrt(sum(x*x for x in qv.values()))
    result = []
    for p, doc in zip(policies, documents):
        v = vector(doc)
        denominator = qnorm * math.sqrt(sum(x*x for x in v.values()))
        score = sum(v.get(t, 0)*x for t, x in qv.items())/denominator if denominator else 0
        result.append((score, p))
    return sorted(result, key=lambda pair: (-pair[0], pair[1]['id']))


class Query(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=100)


class ContextInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    city: Literal['广州', '深圳'] | None = None
    employment_type: Literal['正式员工', '实习生'] | None = None
    planned_start_date: date | None = None


@router.post('/api/assistant/context')
def save_context(body: ContextInput, u=Depends(user)):
    if u['role'] != 'employee':
        fail('forbidden', '只有员工可补充自己的查询条件。', 403)
    changes = body.model_dump(exclude_none=True, mode='json')
    with db.tx() as conn:
        existing = resolve_context(conn, u)
        for key, value in changes.items():
            if u.get(key) and value != u[key]:
                fail('profile_verification_required', '已有员工资料需要 HR 核对，不能在咨询中覆盖。', 409)
        reported = {k: existing[k] for k in CONTEXT_FIELDS if not u.get(k) and existing.get(k)}
        reported.update({k: v for k, v in changes.items() if not u.get(k)})
        conn.execute('INSERT INTO query_context(user_id,context) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET context=excluded.context', (u['id'], db.dump(reported)))
        return resolve_context(conn, u)


def checklist_data(conn, u):
    ctx = resolve_context(conn, u)
    missing = [key for key in CONTEXT_FIELDS if not ctx.get(key)]
    policies = applicable(conn, u, ctx)
    conflict = conflicts(policies)
    items = {}
    refs = []
    if not conflict:
        for p in policies:
            if p['rules'].get('required_items'):
                refs.append(citation(p))
                for item in p['rules']['required_items']:
                    # This is metadata, not document authentication.
                    provided = bool(conn.execute('SELECT 1 FROM materials m JOIN cases c ON c.id=m.case_id WHERE c.requester_id=? AND m.required_item_code=? AND m.provided=1 LIMIT 1', (u['id'], item['code'])).fetchone())
                    items[item['code']] = item | {'provided': provided}
    return {'context': ctx, 'missing_context_fields': missing, 'items': list(items.values()), 'citation_refs': refs,
            'reason_code': 'missing_context' if missing else 'policy_conflict' if conflict else 'ok'}


@router.get('/api/checklist')
def get_checklist(u=Depends(user)):
    with db.connect() as conn:
        return checklist_data(conn, u)


def intent(question):
    if re.search(r'薪资|工资|社保|裁员|赔偿|劳动仲裁|录用决定|绩效|真实身份证', question):
        return 'OUT_OF_SCOPE'
    if re.search(r'补交|缺件|缺失|材料.*(没有|没|缺)|信息表.*(没有|没|缺)', question):
        return 'MATERIAL_EXCEPTION'
    if re.search(r'账号|帐户|电脑|设备|权限开通|邮箱', question):
        return 'ACCESS_DEVICE'
    return 'GENERAL_ONBOARDING'


def answer_query(conn, u, body):
    question = body.question.strip()
    ctx = resolve_context(conn, u)
    category = intent(question)
    result = {'action': 'offer_handoff', 'answer_text': '', 'required_context_fields': [], 'citation_refs': [],
              'draft_case': {'category': 'GENERAL_ONBOARDING' if category == 'OUT_OF_SCOPE' else category, 'fields': {'description': question}},
              'reason_code': 'no_match', 'provider_mode': 'offline', 'retrieval_trace_id': db.uid('trace'), 'context': ctx,
              'category': category}
    if category == 'OUT_OF_SCOPE':
        result.update(action='out_of_scope', answer_text='这个问题超出入职准备与首周服务的模拟制度范围。可以保留问题交给 HR 核对；此处不会给出薪资、社保或劳动关系判断。', reason_code='out_of_scope')
        return result
    # Text may report a profile error, but never changes verified identity.
    contrary_city = re.search(r'(?:我是|我在|我属于|改成|改为)(广州|深圳)', question)
    contrary_type = re.search(r'(?:我是|改成|改为)(正式员工|实习生)', question)
    if (contrary_city and u.get('city') and contrary_city[1] != u['city']) or (contrary_type and u.get('employment_type') and contrary_type[1] != u['employment_type']):
        result.update(answer_text='你提供的信息与已保存员工资料不同，请提交 HR 核对；聊天内容不会改变已验证资料。', reason_code='profile_mismatch')
        return result
    missing = [key for key in CONTEXT_FIELDS if not ctx.get(key)]
    if missing:
        names = {'city': '报到城市', 'employment_type': '雇佣类型', 'planned_start_date': '计划入职日期'}
        result.update(action='clarify', answer_text='请先确认'+'、'.join(names[key] for key in missing)+'，再匹配适用制度。', required_context_fields=missing, reason_code='missing_context')
        return result
    policies = applicable(conn, u, ctx)
    if conflicts(policies):
        result.update(answer_text='发现适用制度版本范围重叠，暂不能提供确定结论。请交给 HR 核对，问题与查询条件会保留在请求草稿中。', reason_code='policy_conflict')
        return result
    if category == 'ACCESS_DEVICE':
        cases = [dict(r) for r in conn.execute("SELECT id,case_number,status,assigned_group,assigned_to,updated_at FROM cases WHERE requester_id=? AND category='ACCESS_DEVICE' AND status NOT IN ('closed','cancelled') ORDER BY created_at DESC", (u['id'],))]
        if cases:
            for c in cases:
                c['tasks'] = [dict(r) for r in conn.execute('SELECT id,type,status,required FROM tasks WHERE case_id=?', (c['id'],))]
                c['blocker'] = '等待员工补充信息' if c['status'] == 'waiting_employee' else '等待员工确认结果' if c['status'] == 'resolved' else '必需演示任务尚未完成' if any(t['required'] and t['status'] != 'completed' for t in c['tasks']) else '等待 HR 经办人处理'
            result.update(action='show_status', answer_text='找到你已有的账号/设备准备请求。以下状态直接来自本地数据库，请先查看进度；有不同的新事项仍可创建请求。', reason_code='existing_case', cases=cases)
            return result
    ranked = rank(question, policies)
    generic_bigrams = {'入职','演示','公司','可以','员工','办理','说明','怎么','哪里','如何','需要','申请','工作','广州','深圳','什么','是否','能否','领取','地点','时间'}
    # A gram crossing a conjunction (e.g. 点与 in 地点与时间) is not topic
    # evidence. Keep such grams in TF-IDF ranking, only exclude from the gate.
    connective_chars = set('的与和吗呢了我你请？?，。、')
    meaningful_query = {g for g in grams(question) if len(g) == 2 and not set(g) & connective_chars} - generic_bigrams
    relevant = [(score, p) for score, p in ranked
                if (score + (0.1 if category in ('MATERIAL_EXCEPTION','ACCESS_DEVICE') and p['category'] == category else 0)) >= RETRIEVAL_CONFIG['threshold']
                and meaningful_query & {g for g in grams(p['title'] + p['content_markdown']) if len(g) == 2}]
    # Topic routing is finite; content still comes only from current authorized policies.
    if category in ('MATERIAL_EXCEPTION', 'ACCESS_DEVICE'):
        relevant = [(score, p) for score, p in relevant if p['category'] == category]
    if not relevant:
        result.update(answer_text='没有找到能支持该问题的适用制度。可以预览请求并转交 HR，当前问题和条件会保留。', reason_code='no_match')
        return result
    selected = relevant[:RETRIEVAL_CONFIG['top_k']]
    refs = []
    for _, p in selected:
        # The ranking document may match any clause. Cite the matching clause,
        # rather than always showing the first heading of the article.
        fragments = sections(p)
        if fragments:
            ranked_fragments = rank(question, [
                {'id': section['section_id'], 'title': '', 'content_markdown': section['excerpt']}
                for section in fragments
            ])
            chosen = next(s for s in fragments if s['section_id'] == ranked_fragments[0][1]['id'])
            refs.append(citation(p, chosen))
        else:
            refs.append(citation(p))
    prefix = f"按计划入职日 {ctx['planned_start_date']} 匹配：{ctx['city']} / {ctx['employment_type']}。"
    if 'self_reported_context' in ctx['source'].values():
        prefix += '部分条件按你提供的信息匹配，待 HR 核对。'
    text = prefix + '\n\n' + '\n\n'.join(ref['excerpt'] for ref in refs)
    if category == 'MATERIAL_EXCEPTION':
        text += '\n\n下一步：填写缺项和预计补交日期，预览并确认申请。允许申请不代表已获批准；材料保持缺失，须等待 HR 决定。'
        match = re.search(r'\d{4}-\d{2}-\d{2}', question)
        result['draft_case']['fields'].update(missing_item='入职信息表' if '信息表' in question else '', expected_date=match[0] if match else '')
    elif category == 'ACCESS_DEVICE':
        text += '\n\n下一步：预览账号/设备准备异常请求，由 HR 协调 IT 演示任务。'
        result['draft_case']['fields']['device_type'] = '设备' if re.search(r'设备|电脑', question) else '账号'
    else:
        text += '\n\n下一步：对照适用清单准备；信息有误或存在异常时，可创建请求请 HR 核对。'
    result.update(action='answer', answer_text=text, citation_refs=refs, reason_code='supported', checklist=checklist_data(conn, u)['items'])
    return result


class CitationRef(BaseModel):
    article_version_id: str
    title: str
    version: str
    section_id: str
    excerpt: str
    url: str
    content_hash: str
    effective_from: str
    effective_to: str | None
    cities: list[str]
    employment_types: list[str]


class QueryResponse(BaseModel):
    action: Literal['answer','clarify','show_status','offer_handoff','out_of_scope']
    answer_text: str
    required_context_fields: list[str]
    citation_refs: list[CitationRef]
    draft_case: dict | None
    reason_code: str
    provider_mode: Literal['offline','offline_fallback','remote']
    retrieval_trace_id: str
    conversation_id: str
    context: dict
    query_context: dict
    category: str
    checklist: list[dict] = Field(default_factory=list)
    cases: list[dict] = Field(default_factory=list)
    provider_failure: str | None = None
    remote_summary_draft: str | None = None
    provider_note: str | None = None


@router.post('/api/assistant/query', response_model=QueryResponse)
def query(body: Query, u=Depends(user)):
    if u['role'] != 'employee':
        fail('forbidden', '请用员工演示身份咨询；HR 可在知识管理中审核原文。', 403)
    from .providers import enhance
    from contextlib import closing
    conversation_id = body.conversation_id or db.uid('conv')
    def check_owner(conn):
        owner = conn.execute('SELECT user_id FROM knowledge_answers WHERE conversation_id=? LIMIT 1', (conversation_id,)).fetchone()
        if owner and owner['user_id'] != u['id']:
            fail('forbidden', '不能访问他人的咨询。', 403)
    # Network/model waiting must not hold SQLite's global writer lock.
    with closing(db.connect()) as conn:
        check_owner(conn)
        baseline = answer_query(conn, u, body)
    result = enhance(body.question, baseline)
    with db.tx() as conn:
        # Recheck after any slow provider call: another request may have claimed
        # this conversation, changed the profile, or withdrawn the cited version.
        check_owner(conn)
        current_user = db.unpack(conn.execute('SELECT * FROM users WHERE id=?', (u['id'],)).fetchone())
        if not current_user or current_user['role'] != 'employee' or current_user['company'] != u['company']:
            fail('forbidden', '当前身份或所属企业已变化，请重新登录。', 403)
        fresh = answer_query(conn, current_user, body)
        stable = lambda response: {key:value for key,value in response.items() if key != 'retrieval_trace_id'}
        if stable(fresh) != stable(baseline):
            result = fresh
            result['provider_mode'] = 'offline_fallback'
            result['provider_failure'] = 'context_changed_during_generation'
            result['provider_note'] = '生成期间资料或制度发生变化，已按最新权限和依据重新核对。'
        result['conversation_id'] = conversation_id
        result['query_context'] = result['context']
        origin = __import__('os').environ.get('DATA_ORIGIN', 'manual')
        metadata = {'conversation_id': conversation_id, 'category': result['category'], 'reason_code': result['reason_code'], 'provider_mode': result['provider_mode'], 'retrieval_trace_id': result['retrieval_trace_id'], 'scope_company': current_user['company'],
                    'assigned_group': (result['context'].get('city') or '广州')+'HR', 'context': result['context']}
        db.log(conn, 'question_submitted', u['id'], metadata=metadata, data_origin=origin)
        db.log(conn, 'context_requested' if result['action'] == 'clarify' else 'answer_shown', u['id'], metadata=metadata | {'action': result['action']}, data_origin=origin)
        conn.execute('INSERT INTO knowledge_answers VALUES(?,?,?,?,?,?)', (result['retrieval_trace_id'], conversation_id, u['id'], body.question, db.dump(result), db.now()))
        return result


@router.get('/api/assistant/history')
def history(u=Depends(user)):
    with db.connect() as conn:
        context = resolve_context(conn, u)
        rows = []
        for row in conn.execute('SELECT * FROM knowledge_answers WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (u['id'],)):
            response = json.loads(row['response'])
            references = response.get('citation_refs', [])
            visible = True
            for ref in references:
                policy = decode(conn.execute('SELECT * FROM policies WHERE id=?', (ref['article_version_id'],)).fetchone())
                if not policy or not scope_match(policy, u, context) or not publication_available(policy):
                    visible = False
                    break
            if not visible:
                # Preserve the audit row in storage, but do not leak revoked
                # content via cached prose, checklist or remote summaries.
                response = {'action': 'offer_handoff', 'reason_code': 'historical_access_changed',
                            'answer_text': '当前员工资料或访问范围已变化，原回答暂不可展示。请按当前条件重新查询或联系 HR 核对。',
                            'citation_refs': [], 'required_context_fields': [], 'draft_case': None,
                            'provider_mode': 'offline', 'retrieval_trace_id': row['id'],
                            'conversation_id': row['conversation_id'], 'context': context}
            rows.append({'question': row['question'], 'response': response, 'created_at': row['created_at']})
        return rows


@router.get('/api/knowledge/{version_id}')
def read_policy(version_id: str, u=Depends(user)):
    with db.connect() as conn:
        p = decode(conn.execute('SELECT * FROM policies WHERE id=?', (version_id,)).fetchone())
        if not p or p['scope_company'] != u['company'] or u['role'] not in p['visibility_roles']:
            fail('forbidden', '无权查看该制度版本。', 403)
        if u['role'] not in ('hr', 'admin'):
            if p['status'] not in ('published', 'withdrawn') or not publication_available(p) or not scope_match(p, u, resolve_context(conn, u)):
                fail('forbidden', '无权查看该制度版本。', 403)
            # Past and withdrawn articles are only audit material after an actual citation.
            ctx = resolve_context(conn, u)
            current = p['status'] == 'published' and ctx.get('planned_start_date') and p['effective_from'] <= ctx['planned_start_date'] and (not p['effective_to'] or ctx['planned_start_date'] < p['effective_to'])
            if not current:
                cited = any(any(ref.get('article_version_id') == version_id for ref in json.loads(r['response']).get('citation_refs', [])) for r in conn.execute('SELECT response FROM knowledge_answers WHERE user_id=?', (u['id'],)))
                cited = cited or any(any(ref.get('article_version_id') == version_id for ref in json.loads(r['policy_snapshot_refs'])) for r in conn.execute('SELECT policy_snapshot_refs FROM cases WHERE requester_id=?', (u['id'],)))
                if not cited:
                    fail('forbidden', '该历史版本仅用于已有回答或请求的只读追溯。', 403)
        return p | {'sections': sections(p), 'audit_only': p['status'] != 'published' or bool(p['effective_to'] and p['effective_to'] <= (resolve_context(conn, u).get('planned_start_date') or ''))}


class PolicyDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_key: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    version: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=2, max_length=150)
    category: Literal['GENERAL_ONBOARDING', 'MATERIAL_EXCEPTION', 'ACCESS_DEVICE'] = 'GENERAL_ONBOARDING'
    cities: list[str] = Field(default_factory=lambda: ['*'], min_length=1)
    employment_types: list[str] = Field(default_factory=lambda: ['*'], min_length=1)
    visibility_roles: list[str] = Field(default_factory=lambda: ['employee', 'hr', 'admin'], min_length=1)
    effective_from: date
    effective_to: date | None = None
    content_markdown: str = Field(min_length=5, max_length=20000)
    rules: dict = Field(default_factory=dict)
    replaces_id: str | None = None
    row_version: int = 1

    @model_validator(mode='after')
    def validate_fields(self):
        if not set(self.cities) <= CITIES | {'*', '全部'} or not set(self.employment_types) <= TYPES | {'*', '全部'} or not set(self.visibility_roles) <= ROLES:
            raise ValueError('适用范围或可见角色不合法')
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError('结束日期必须晚于开始日期')
        if self.rules.get('hr_approval_required') is False:
            raise ValueError('补交审批不能配置为自动批准')
        if 'required_items' in self.rules:
            items = self.rules['required_items']
            if not isinstance(items, list):
                raise ValueError('材料规则必须是包含编码和名称的条目列表')
            seen = set()
            for item in items:
                if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip() for k in ('code', 'label')):
                    raise ValueError('每个材料条目必须有非空字符串编码和名称')
                if item['code'] in seen:
                    raise ValueError('同一规则的材料编码不能重复')
                seen.add(item['code'])
        return self


class VersionAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    row_version: int


def rule_text(rules):
    lines = []
    if rules.get('required_items'):
        lines.append('适用材料清单：'+'、'.join(item['label'] for item in rules['required_items'])+'。这里只记录材料元信息，不核验真伪。')
    if rules.get('hr_approval_required'):
        lines.append('补交申请须由 HR 审批。允许申请不等于已获批准，批准延期不表示材料已收齐。')
    if rules.get('required_fields'):
        names = {'description': '事项说明', 'missing_item': '缺失材料', 'expected_date': '预计补交日期', 'device_type': '账号或设备类型'}
        lines.append('申请必填项：'+'、'.join(names.get(f, f) for f in rules['required_fields'])+'。')
    if rules.get('required_it_task'):
        lines.append('必需 IT 子任务完成后，HR 才能提交结果说明，随后由员工确认。此处仅完成演示任务，不开通真实账号。')
    return '\n'.join(lines)


def authored_content(content, rules):
    text = content.strip()
    if not text.startswith(DISCLAIMER):
        text = DISCLAIMER+'\n\n'+text
    if '## ' not in text:
        text = DISCLAIMER+'\n\n## 办理说明\n'+text.removeprefix(DISCLAIMER).strip()
    if rule_text(rules):
        text += '\n\n## 结构化办理规则\n'+rule_text(rules)
    return text


def insert_policy(conn, p):
    keys = list(p)
    values = [db.dump(p[k]) if isinstance(p[k], (dict, list)) else p[k] for k in keys]
    conn.execute(f"INSERT INTO policies ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", values)


@router.get('/api/admin/knowledge')
def list_policies(u=Depends(user)):
    require_hr(u)
    with db.connect() as conn:
        return [decode(r) for r in conn.execute('SELECT * FROM policies WHERE scope_company=? ORDER BY policy_key,effective_from DESC', (u['company'],))]


@router.post('/api/admin/knowledge/drafts')
def create_policy(body: PolicyDraft, u=Depends(user)):
    require_hr(u)
    p = body.model_dump(mode='json')
    p['rules'] = p['rules'] | RULES.get(p['category'], {})
    p['content_markdown'] = authored_content(p['content_markdown'], p['rules'])
    p.update(id=db.uid('pol'), scope_company=u['company'], status='draft', published_at=None,
             owner_id=u['id'], approved_by=None, content_hash=hashlib.sha256(p['content_markdown'].encode()).hexdigest(), created_at=db.now(), updated_at=db.now(), row_version=1)
    with db.tx() as conn:
        if p['replaces_id'] and not conn.execute('SELECT 1 FROM policies WHERE id=? AND scope_company=?', (p['replaces_id'], u['company'])).fetchone():
            fail('invalid_replacement', '待衔接的原制度不存在或不在当前企业范围。', 422)
        if conn.execute('SELECT 1 FROM policies WHERE scope_company=? AND policy_key=? AND version=?', (u['company'], p['policy_key'], p['version'])).fetchone():
            fail('version_conflict', '制度编号与版本已存在，请使用新版本。', 409)
        insert_policy(conn, p)
    return p


@router.put('/api/admin/knowledge/{version_id}')
def edit_policy(version_id: str, body: PolicyDraft, u=Depends(user)):
    require_hr(u)
    with db.tx() as conn:
        p = decode(conn.execute('SELECT * FROM policies WHERE id=? AND scope_company=?', (version_id, u['company'])).fetchone())
        if not p:
            fail('not_found', '未找到制度。', 404)
        if p['status'] != 'draft':
            fail('immutable_version', '已发布内容不可修改，请创建新版本。', 409)
        if p['row_version'] != body.row_version:
            fail('version_conflict', '草稿已被更新，请刷新。', 409)
        updates = body.model_dump(mode='json')
        if updates['replaces_id'] and not conn.execute('SELECT 1 FROM policies WHERE id=? AND scope_company=?', (updates['replaces_id'], u['company'])).fetchone():
            fail('invalid_replacement', '待衔接的原制度不存在或不在当前企业范围。', 422)
        updates['rules'] |= RULES.get(updates['category'], {})
        # Remove the generated block before rebuilding it from authoritative fields.
        updates['content_markdown'] = authored_content(updates['content_markdown'].split('\n\n## 结构化办理规则')[0], updates['rules'])
        updates.update(content_hash=hashlib.sha256(updates['content_markdown'].encode()).hexdigest(), updated_at=db.now(), row_version=p['row_version']+1)
        try:
            conn.execute('UPDATE policies SET '+','.join(k+'=?' for k in updates)+' WHERE id=?', [db.dump(v) if isinstance(v,(dict,list)) else v for v in updates.values()]+[version_id])
        except __import__('sqlite3').IntegrityError:
            fail('version_conflict', '制度编号与版本已存在。', 409)
        return decode(conn.execute('SELECT * FROM policies WHERE id=?', (version_id,)).fetchone())


def overlap(a, b):
    return bool(expanded(a['cities'], CITIES) & expanded(b['cities'], CITIES)
                and expanded(a['employment_types'], TYPES) & expanded(b['employment_types'], TYPES)
                and a['effective_from'] < (b['effective_to'] or '9999-12-31')
                and b['effective_from'] < (a['effective_to'] or '9999-12-31'))


@router.post('/api/admin/knowledge/{version_id}/publish')
def publish_policy(version_id: str, body: VersionAction, u=Depends(user)):
    if u['role'] != 'admin':
        fail('forbidden', '只有 HR 管理员可审核发布。', 403)
    with db.tx() as conn:
        p = decode(conn.execute('SELECT * FROM policies WHERE id=? AND scope_company=?', (version_id, u['company'])).fetchone())
        if not p:
            fail('not_found', '未找到制度。', 404)
        if p['status'] != 'draft' or p['row_version'] != body.row_version:
            fail('version_conflict', '只能发布当前版本的草稿，请刷新。', 409)
        if p['owner_id'] == u['id']:
            fail('separation_of_duties', '请使用另一名 HR 经办人创建草稿，再由管理员审核。', 409)
        if p['replaces_id']:
            old = decode(conn.execute('SELECT * FROM policies WHERE id=?', (p['replaces_id'],)).fetchone())
            if not old or old['status'] != 'published' or old['scope_company'] != p['scope_company'] or old['policy_key'] != p['policy_key'] or expanded(old['cities'], CITIES) != expanded(p['cities'], CITIES) or expanded(old['employment_types'], TYPES) != expanded(p['employment_types'], TYPES):
                fail('invalid_replacement', '衔接版本必须具有相同制度编号和适用范围。', 409)
            if p['effective_from'] <= old['effective_from'] or (old['effective_to'] and p['effective_from'] > old['effective_to']):
                fail('invalid_replacement', '新版本生效日期不能早于旧版本或产生空档。', 409)
            if p['effective_from'] < datetime.fromisoformat(db.now()).date().isoformat():
                fail('invalid_replacement', '版本衔接不能追溯修改过去的适用区间。', 409)
            conn.execute('UPDATE policies SET effective_to=?,updated_at=?,row_version=row_version+1 WHERE id=?', (p['effective_from'],db.now(),old['id']))
        for row in conn.execute("SELECT * FROM policies WHERE scope_company=? AND policy_key=? AND status='published' AND id<>?", (u['company'],p['policy_key'],version_id)):
            if overlap(p, decode(row)):
                fail('policy_conflict', '同一制度的城市、雇佣类型及日期范围存在交集，不能发布。', 409)
        conn.execute("UPDATE policies SET status='published',published_at=?,approved_by=?,updated_at=?,row_version=row_version+1 WHERE id=?", (db.now(),u['id'],db.now(),version_id))
        db.log(conn,'knowledge_published',u['id'],metadata={'article_version_id':version_id,'policy_key':p['policy_key']},visibility='internal',data_origin=__import__('os').environ.get('DATA_ORIGIN','manual'))
        return decode(conn.execute('SELECT * FROM policies WHERE id=?', (version_id,)).fetchone())


@router.post('/api/admin/knowledge/{version_id}/withdraw')
def withdraw_policy(version_id: str, body: VersionAction, u=Depends(user)):
    if u['role'] != 'admin':
        fail('forbidden','只有 HR 管理员可撤回。',403)
    with db.tx() as conn:
        changed=conn.execute("UPDATE policies SET status='withdrawn',row_version=row_version+1,updated_at=? WHERE id=? AND scope_company=? AND status='published' AND row_version=?", (db.now(),version_id,u['company'],body.row_version)).rowcount
        if not changed:
            fail('version_conflict','状态或版本已变化，请刷新。',409)
        db.log(conn,'knowledge_withdrawn',u['id'],metadata={'article_version_id':version_id},visibility='internal')
        return decode(conn.execute('SELECT * FROM policies WHERE id=?',(version_id,)).fetchone())


def seed_knowledge(conn):
    if conn.execute('SELECT 1 FROM policies LIMIT 1').fetchone():
        return
    def add(id,key,version,title,city,etype,text,category='GENERAL_ONBOARDING',start='2026-01-01',end=None,roles=None,rules=None,status='published'):
        rules = (rules or {}) | RULES[category]
        content = authored_content('## 办理说明\n'+text,rules)
        p = dict(id=id,policy_key=key,version=version,title=title,category=category,scope_company='xingzhou',cities=city,employment_types=etype,
                 visibility_roles=roles or ['employee','hr','admin'],status=status,published_at='2026-10-01T00:00:00+00:00' if status=='published' else None,
                 effective_from=start,effective_to=end,owner_id='hr01',approved_by='admin01' if status=='published' else None,content_markdown=content,
                 content_hash=hashlib.sha256(content.encode()).hexdigest(),rules=rules,created_at='2026-10-01T00:00:00+00:00',updated_at='2026-10-01T00:00:00+00:00',replaces_id=None,row_version=1)
        insert_policy(conn,p)
    add('pol-report-gz-v1','report-gz','1.0','广州报到地点与联系人',['广州'],['*'],'请于计划入职日 09:30 到广州演示园区 A 座 1 楼报到，联系人为广州 HR 演示服务台。',end='2026-11-01')
    add('pol-report-gz-v2','report-gz','2.0','广州报到地点与联系人',['广州'],['*'],'请于计划入职日 09:30 到广州演示园区 A 座 2 楼报到，联系人为广州 HR 演示服务台。先签到，再核对材料清单，随后领取演示设备。',start='2026-11-01')
    add('pol-report-sz-v1','report-sz','1.0','深圳报到地点与联系人',['深圳'],['*'],'请于计划入职日 10:00 到深圳演示园区 B 座 3 楼报到，联系人为深圳 HR 演示服务台。',end='2027-01-01')
    for city,short in [('广州','gz'),('深圳','sz')]:
        for etype,code in [('正式员工','full'),('实习生','intern')]:
            items = [{'code':'onboarding_form','label':'入职信息表'},{'code':'contact_confirmation','label':'联系信息确认'}]
            if etype=='实习生':
                items.append({'code':'internship_arrangement','label':'实习安排确认'})
            else:
                items.append({'code':'employment_information','label':'任职信息确认'})
            add(f'pol-material-{short}-{code}',f'material-{short}-{code}','1.0',f'{city}{etype}入职材料清单',[city],[etype],f'{city}{etype}应准备：'+ '、'.join(i['label'] for i in items)+'。只记录是否准备好，不上传真实证件。',rules={'required_items':items})
    for etype,code in [('正式员工','full'),('实习生','intern')]:
        add(f'pol-exception-{code}',f'exception-{code}','1.0',f'{etype}材料补交申请路径',['*'],[etype],'缺失材料时可填写缺失项、原因和预计补交日期，提交 HR 审批。HR 可批准本次延期安排或拒绝申请；未经审批不能视作已获批准。延期获批不代表材料已收齐，员工仍需补交。','MATERIAL_EXCEPTION')
    for city,short in [('广州','gz'),('深圳','sz')]:
        add(f'pol-access-{short}',f'access-{short}','1.0',f'{city}账号设备准备与异常处理',[city],['*'],'账号、邮箱或电脑设备尚未准备好时，先查看已有请求。没有记录时提交异常，由 HR 负责协调 IT；完成结果仅为“演示任务已完成”，不代表真实账号开通。','ACCESS_DEVICE')
    add('pol-feedback','service-feedback','1.0','请求进度查询与结果反馈',['*'],['*'],'员工可在我的请求查看处理团队、缺项和下一步。HR 给出结果后员工确认完成，也可说明原因申请重开。请反馈容易程度 1–5 分和问题是否解决。')
    add('pol-first-week','first-week','1.0','入职首周办理流程',['*'],['*'],'第一天报到签到、核对材料并查看账号设备准备情况；首周遇到流程问题可向 HR 提交一般入职咨询请求。')
    add('pol-hr-internal','hr-internal','1.0','HR 内部补交审批记录说明',['*'],['*'],'内部经办说明：记录审批依据和补交日期，内部备注不能作为员工通知。不可把材料缺失自动标为已提供。',roles=['hr','admin'])
    add('pol-report-sz-v2','report-sz','2.0','深圳报到地点与联系人（未来版本）',['深圳'],['*'],'请于计划入职日 10:00 到深圳演示园区 B 座 5 楼报到，联系人为深圳 HR 演示服务台。',start='2027-01-01')
    add('pol-draft-welcome','welcome-draft','0.1','欢迎流程草稿',['*'],['*'],'尚未审核的欢迎说明，不得用于员工答案。',status='draft')
