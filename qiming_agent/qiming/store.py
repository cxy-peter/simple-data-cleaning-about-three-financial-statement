"""SQLite authoritative documents, approval gates, tenant isolation and execution records."""
from __future__ import annotations
import contextlib, hashlib, json, sqlite3, uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

@dataclass(frozen=True)
class Principal:
    user_id:str
    tenant:str='demo'
    departments:tuple[str,...]=('research',)
    role:str='analyst'

def now():return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self,path:str|Path=':memory:'):
        if str(path)!=':memory:':Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        import threading
        self.lock=threading.RLock()
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, tenant TEXT, department TEXT,
          topic TEXT, version INTEGER, title TEXT, body TEXT, source TEXT, status TEXT,
          valid_from TEXT, valid_until TEXT, sha256 TEXT, created_by TEXT, reviewed_by TEXT,
          UNIQUE(tenant,department,topic,version));
        CREATE TABLE IF NOT EXISTS traces(id TEXT PRIMARY KEY,tenant TEXT,user_id TEXT,created_at TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,tenant TEXT,trace_id TEXT,category TEXT,
          note TEXT,created_by TEXT,status TEXT,reviewed_by TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT,actor TEXT,
          action TEXT,target TEXT,created_at TEXT);
        ''')

    def close(self):self.db.close()

    def add_document(self,p:Principal,doc:dict,status='pending_review'):
        if p.role!='editor':raise PermissionError('仅编辑可导入文档')
        if doc['topic'].startswith('report:') and p.user_id!='seed-editor':raise PermissionError('财务事实需走独立核验导入，不能由知识文本覆盖')
        dept=doc.get('department','research')
        if dept not in p.departments:raise PermissionError('不能写入未授权部门')
        if status not in {'pending_review','active','archived'}:raise ValueError('未知状态')
        body=str(doc.get('body','')).strip()
        if not body or len(body)>100000:raise ValueError('正文为空或超过限制')
        valid_from=doc.get('valid_from','2020-01-01');valid_until=doc.get('valid_until')
        date.fromisoformat(valid_from)
        if valid_until:
            date.fromisoformat(valid_until)
            if valid_until<valid_from:raise ValueError('生效/失效日期倒置')
        did=doc.get('id') or uuid.uuid4().hex
        with self.lock,self.db:
            self.db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
              did,p.tenant,dept,doc['topic'],int(doc['version']),doc['title'],body,doc.get('source','用户上传/未核对'),
              status,valid_from,valid_until,hashlib.sha256(body.encode()).hexdigest(),p.user_id,
              'seed_review' if status=='active' else None))
        return did

    def publish(self,p:Principal,did:str):
        if p.role!='editor':raise PermissionError('仅编辑可发布')
        with self.lock,self.db:
            d=self.db.execute('SELECT * FROM documents WHERE id=? AND tenant=?',(did,p.tenant)).fetchone()
            if not d or d['department'] not in p.departments:raise PermissionError('文档不存在或无权访问')
            if d['status']!='pending_review':raise ValueError('仅待审核文档可发布')
            if d['created_by']==p.user_id:raise PermissionError('需要独立第二人复核，不能自审')
            today=date.today().isoformat()
            if d['valid_from']>today or (d['valid_until'] and d['valid_until']<today):raise ValueError('尚未生效或已过期')
            current=self.db.execute('SELECT MAX(version) FROM documents WHERE tenant=? AND department=? AND topic=? AND status="active"',
             (p.tenant,d['department'],d['topic'])).fetchone()[0]
            if current is not None and current>=d['version']:raise ValueError('新版号必须大于当前发布版本')
            self.db.execute('UPDATE documents SET status="archived" WHERE tenant=? AND department=? AND topic=? AND status="active"',
             (p.tenant,d['department'],d['topic']))
            self.db.execute('UPDATE documents SET status="active",reviewed_by=? WHERE id=?',(p.user_id,did))
            self.db.execute('INSERT INTO audit(tenant,actor,action,target,created_at) VALUES(?,?,?,?,?)',
             (p.tenant,p.user_id,'publish',did,now()))
        return did

    def visible(self,p:Principal,as_of:str|None=None,include_pending=False):
        as_of=as_of or date.today().isoformat();date.fromisoformat(as_of)
        with self.lock:
            rows=[dict(r) for r in self.db.execute('SELECT * FROM documents WHERE tenant=?',(p.tenant,))]
        rows=[r for r in rows if r['department'] in p.departments]
        if include_pending and p.role=='editor':return rows
        return [r for r in rows if r['status']=='active' and r['valid_from']<=as_of and (not r['valid_until'] or r['valid_until']>=as_of)]

    def save_trace(self,p:Principal,payload:dict):
        tid=payload.get('trace_id') or uuid.uuid4().hex
        with self.lock,self.db:self.db.execute('INSERT INTO traces VALUES (?,?,?,?,?)',(tid,p.tenant,p.user_id,now(),json.dumps(payload,ensure_ascii=False)))
        return tid

    def traces(self,p:Principal):
        with self.lock:
            rows=self.db.execute('SELECT id,user_id,created_at,payload FROM traces WHERE tenant=? AND user_id=? ORDER BY created_at DESC LIMIT 40',(p.tenant,p.user_id)).fetchall()
        return [dict(r)|{'payload':json.loads(r['payload'])} for r in rows]

    def feedback(self,p:Principal,tid,category,note):
        if category not in {'retrieval','intent','generation','knowledge_gap','data_quality'}:raise ValueError('未知反馈分类')
        with self.lock,self.db:
            t=self.db.execute('SELECT id FROM traces WHERE id=? AND tenant=? AND user_id=?',(tid,p.tenant,p.user_id)).fetchone()
            if not t:raise PermissionError('只能反馈本人任务')
            pid=uuid.uuid4().hex
            self.db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?)',(pid,p.tenant,tid,category,note[:2000],p.user_id,'pending_review',None,now()))
        return pid

    def proposals(self,p):
        if p.role!='editor':raise PermissionError('仅编辑查看反馈队列')
        with self.lock:return [dict(r) for r in self.db.execute('SELECT * FROM proposals WHERE tenant=? ORDER BY created_at DESC',(p.tenant,))]

    def review_proposal(self,p,pid,decision):
        if p.role!='editor':raise PermissionError('仅编辑可复核')
        if decision not in {'accepted_for_development','rejected'}:raise ValueError('只能确认待开发或驳回；不自动改规则')
        with self.lock,self.db:
            row=self.db.execute('SELECT * FROM proposals WHERE id=? AND tenant=?',(pid,p.tenant)).fetchone()
            if not row:raise PermissionError('无权访问')
            if row['status']!='pending_review':raise ValueError('已处理')
            if row['created_by']==p.user_id:raise PermissionError('不能自审')
            self.db.execute('UPDATE proposals SET status=?,reviewed_by=? WHERE id=?',(decision,p.user_id,pid))
        return {'id':pid,'status':decision,'note':'仅进入开发待办，线上Skill未改变；需修改配置、测试、代码评审后发布'}
