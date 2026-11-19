from copy import deepcopy
from app.modules.m6_reconcile.compare import balance
from app.workflow import run,load

HEADER = 'id,sample_id,batch,product,analyte,method,version,sampled,reported,result,unit,lod,relation,note'

def _row(rid,sample,batch,analyte,version,reported,result,lod,relation='',method='LC-MS/MS',unit='mg/kg'):
    return ','.join([rid,sample,batch,'苹果',analyte,method,str(version),'2026-08-01',reported,result,unit,str(lod),relation,'测试'])

def test_count_conservation(state):
    assert state['reconcile']['balanced'] is True
def test_input_total(state):
    r=state['reconcile'];assert r['input']==r['accepted']+r['isolated']+r['blank']+r['headers']+r['duplicates']
def test_quarantine_visible(state):
    assert state['reconcile']['isolated']>=4
def test_broken_count_detected(state):
    s=deepcopy(state);s['input_count']+=1;assert balance(s)['balanced'] is False
def test_clean_count():
    s=run(load('clean.csv'),{});assert s['reconcile']['balanced'] and s['reconcile']['isolated']==0
def test_business_has_totals(state):
    assert state['reconcile']['business'] and 'total' in state['reconcile']['business']
def test_no_evidence_lost(state):
    assert all(r['line']>=2 and r['code'] for r in state['quarantine'])

def test_identity_attribution_shared_across_grades_and_rulings():
    content=(HEADER+'\n'+'\n'.join([
        _row('09001','S901','B901','P01',1,'2026-08-02','<0.01',0.01),
        _row('09001','S901','B901','P01',2,'2026-08-04','0.004',0.001,relation='09001'),
        _row('09002','S902','B901','P01',1,'2026-08-02','<0.01',0.01),
        _row('09002','S999','B999','P01',2,'2026-08-04','0.004',0.001),
    ])+'\n').encode()
    s=run(content,{})
    ident=s['reconcile']['identity']
    assert ident['kept']==1 and ident['rejected']==1
    # 批次等级只来自采纳证据：冒用件的 B999 不产生等级，B901 保留同一身份归属
    assert {g['batch'] for g in s['reconcile']['grades']}=={'B901'}
    calc_ids={c['id'] for c in s['calculations']}
    assert calc_ids=={'09001','09002'}
    assert s['reconcile']['balanced'] is True
