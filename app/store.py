import sqlite3,json,os
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # 跨批次检测编号登记：编号 -> 身份锚点与首次归档的运行。重跑只命中同一行，不重复并入。
    db.execute('CREATE TABLE IF NOT EXISTS report_identity (run_id TEXT NOT NULL, report_id TEXT NOT NULL, anchor TEXT NOT NULL, line INTEGER NOT NULL, decision TEXT NOT NULL, PRIMARY KEY (run_id, report_id, anchor, line)) WITHOUT ROWID')
    return db

def _register_identities(db, run_id, state):
    for row in state.get('calculations',[]):
        anchor=[row['sample_id'],row['batch'],row.get('product','苹果'),row['analyte']]
        # 采纳即出证：即使同编号存在冒用件，锚点与裁决基准一致的原始报告仍沿用原编号
        decision='沿用原编号'
        db.execute('INSERT OR IGNORE INTO report_identity(run_id,report_id,anchor,line,decision) VALUES (?,?,?,?,?)',
                   (run_id,row['id'],json.dumps(anchor,ensure_ascii=False),row['line'],decision))
    for q in state.get('quarantine',[]):
        if q['code']!='IDENTITY':continue
        raw=q.get('raw',{})
        anchor=[raw.get('sample_id',''),raw.get('batch',''),raw.get('product',''),raw.get('analyte','')]
        db.execute('INSERT OR IGNORE INTO report_identity(run_id,report_id,anchor,line,decision) VALUES (?,?,?,?,?)',
                   (run_id,q['id'],json.dumps(anchor,ensure_ascii=False),q['line'],'编号冒用隔离'))

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        # 幂等登记：同一 run 重跑不会产生第二条编号登记，旧批次证据永不被覆盖
        _register_identities(db,run_id,body)
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def identity_registry(report_id=None):
    with connection() as db:
        if report_id:
            rows=db.execute('SELECT report_id,anchor,run_id,line,decision FROM report_identity WHERE report_id=? ORDER BY line',(report_id,)).fetchall()
        else:
            rows=db.execute('SELECT report_id,anchor,run_id,line,decision FROM report_identity ORDER BY report_id,line LIMIT 500').fetchall()
    out=[]
    for r in rows:
        item=dict(r)
        try:item['anchor']=json.loads(item['anchor'])
        except (TypeError,ValueError):pass
        out.append(item)
    return out

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)
