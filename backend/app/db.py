"""Single SQLite store; explicit atomic transactions and injectable UTC clock."""
import os, json, sqlite3, uuid
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
_clock = lambda: datetime.now(timezone.utc)
def now(): return _clock().astimezone(timezone.utc).isoformat()
def uid(prefix='id'): return prefix + '_' + uuid.uuid4().hex[:20]
def dump(value): return json.dumps(value, ensure_ascii=False, sort_keys=True)
def error(code, message, status=400): raise HTTPException(status, detail={'code': code, 'message': message})
JSON_FIELDS = {'structured_fields','policy_snapshot_refs','context_source','dependency_task_ids','minimal_context','metadata','fields','missing_field_codes','context','cities','employment_types','visibility_roles','rules','value'}
def unpack(row):
    if row is None: return None
    value = dict(row)
    for k in JSON_FIELDS & value.keys():
        if isinstance(value[k],str):
            try: value[k]=json.loads(value[k])
            except ValueError: pass
    return value

def connect():
    path = os.getenv('DATABASE_PATH', str(ROOT/'data'/'onboardflow.sqlite3'))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    return c

@contextmanager
def tx():
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except BaseException:
        c.rollback(); raise
    finally: c.close()

def log(c,kind,actor_id,case_id=None,metadata=None,visibility='public',data_origin='demo',occurred_at=None):
    c.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)', (uid('ev'),kind,actor_id,case_id,dump(metadata or {}),visibility,occurred_at or now(),data_origin,1))

SCHEMA='''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,company TEXT NOT NULL,city TEXT,employment_type TEXT,planned_start_date TEXT,group_name TEXT);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),csrf TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,case_number TEXT UNIQUE,requester_id TEXT REFERENCES users(id),category TEXT,status TEXT,assigned_group TEXT,assigned_to TEXT REFERENCES users(id),description TEXT,structured_fields TEXT,policy_snapshot_refs TEXT,context_source TEXT,created_at TEXT,updated_at TEXT,first_human_response_at TEXT,due_at TEXT,latest_resolved_at TEXT,closed_at TEXT,cancelled_at TEXT,resolution_code TEXT,reopen_count INTEGER DEFAULT 0,idempotency_key TEXT,row_version INTEGER DEFAULT 1,sla_hours REAL,data_origin TEXT,UNIQUE(requester_id,idempotency_key));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,case_id TEXT REFERENCES cases(id),type TEXT,assignee_id TEXT REFERENCES users(id),status TEXT,required INTEGER,dependency_task_ids TEXT,due_at TEXT,completed_at TEXT,minimal_context TEXT,row_version INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,case_id TEXT REFERENCES cases(id),actor_id TEXT REFERENCES users(id),body TEXT,visibility TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,kind TEXT,actor_id TEXT REFERENCES users(id),case_id TEXT REFERENCES cases(id),metadata TEXT,visibility TEXT,occurred_at TEXT,data_origin TEXT,schema_version INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,case_id TEXT REFERENCES cases(id),required_item_code TEXT,provided INTEGER,format_check_status TEXT,supplied_at TEXT,UNIQUE(case_id,required_item_code));
CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,requester_id TEXT REFERENCES users(id),category TEXT,draft_version INTEGER,context_source TEXT,fields TEXT,created_at TEXT,updated_at TEXT,case_id TEXT REFERENCES cases(id),confirmation_token TEXT);
CREATE TABLE IF NOT EXISTS submission_attempts(id TEXT PRIMARY KEY,draft_id TEXT REFERENCES drafts(id),requester_id TEXT REFERENCES users(id),attempt_no INTEGER,attempted_at TEXT,validation_result TEXT,missing_field_codes TEXT,idempotency_key TEXT,payload_hash TEXT,result_case_id TEXT REFERENCES cases(id),data_origin TEXT,UNIQUE(requester_id,idempotency_key),UNIQUE(draft_id,attempt_no));
CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,case_id TEXT REFERENCES cases(id),conversation_id TEXT,user_id TEXT REFERENCES users(id),ease INTEGER,outcome TEXT,created_at TEXT,data_origin TEXT,UNIQUE(user_id,case_id),UNIQUE(user_id,conversation_id));
CREATE TABLE IF NOT EXISTS query_context(user_id TEXT PRIMARY KEY REFERENCES users(id),context TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
CREATE INDEX IF NOT EXISTS case_owner ON cases(requester_id);
CREATE INDEX IF NOT EXISTS event_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS event_case ON events(case_id);
PRAGMA user_version=1;
'''
def init_db():
    with tx() as c:
        c.executescript(SCHEMA)
        from .knowledge import init_knowledge
        init_knowledge(c)
        c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('sla_hours',dump({'MATERIAL_EXCEPTION':48,'ACCESS_DEVICE':24,'GENERAL_ONBOARDING':48})))
