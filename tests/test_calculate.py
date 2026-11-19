from copy import deepcopy
from app.workflow import run, load
from app.modules.m5_calculate.rules import select_reports, apply, anchor_of

HEADER = 'id,sample_id,batch,product,analyte,method,version,sampled,reported,result,unit,lod,relation,note'


def csv_of(rows):
    return (HEADER + '\n' + '\n'.join(rows) + '\n').encode()


def row(rid, sample, batch, analyte, version, reported, result, lod,
        relation='', method='LC-MS/MS', sampled='2026-08-01', unit='mg/kg'):
    return ','.join([rid, sample, batch, '苹果', analyte, method, str(version),
                     sampled, reported, result, unit, str(lod), relation, '测试报告'])


def chosen_bad(reports):
    chosen, bad, rulings, lineage, bad_lines = select_reports(reports)
    return chosen, bad, rulings, lineage, bad_lines


def test_real_retest_replaces_initial(raw):
    s = run(raw, {})
    assert len(s['calculations']) == 24 and s['calculations'][0]['id'] == '00002' \
        and s['calculations'][0]['replaces'] == '00001'


def test_real_other_analyte_not_erased(raw):
    s = run(raw, {})
    assert s['business'] == {'total': 24, 'pass': 12, 'fail': 12, 'uncertain': 0, 'unit': '检测项目'}


def test_wrong_sample_reference_rejected(raw):
    s = run(raw, {}, 4)
    s['measurements'][1]['sample_id'] = 'OTHER'
    chosen, bad = chosen_bad(s['measurements'])[:2]
    assert bad['00002'] == 'REFERENCE' and any(r['id'] == '00001' for r in chosen)


def test_wrong_method_reference_rejected(raw):
    s = run(raw, {}, 4)
    s['measurements'][1]['method'] = 'GC-MS'
    _, bad = chosen_bad(s['measurements'])[:2]
    assert bad['00002'] == 'REFERENCE'


def test_future_reference_rejected(raw):
    s = run(raw, {}, 4)
    s['measurements'][1]['reported'] = '2026-08-01'
    _, bad = chosen_bad(s['measurements'])[:2]
    assert bad['00002'] == 'REFERENCE'


def test_cycle_rejected(raw):
    # TRAP-20
    s = run(raw, {}, 4)
    s['measurements'][0]['relation'] = '00002'
    _, bad = chosen_bad(s['measurements'])[:2]
    assert '00001' in bad and '00002' in bad


def test_input_order_cannot_change_verdict(raw):
    a = run(raw, {}, 4)
    b = deepcopy(a)
    b['measurements'].reverse()
    ca, ba = chosen_bad(a['measurements'])[:2]
    cb, bb = chosen_bad(b['measurements'])[:2]
    assert sorted(r['id'] for r in ca) == sorted(r['id'] for r in cb) and ba == bb


def test_same_report_id_latest_revision_is_authoritative(raw):
    s = run(raw, {}, 4)
    new = dict(s['measurements'][1], version='3', lower='0.006', upper='0.006', code='FAIL')
    s['measurements'].append(new)
    chosen, bad = chosen_bad(s['measurements'])[:2]
    assert next(r for r in chosen if r['id'] == '00002')['code'] == 'FAIL' and '00002' not in bad


# ---- 补发编号裁决：沿用原编号 vs 编号冒用 ----

def test_genuine_reissue_keeps_original_number():
    # 同编号、同样本/项目，版本递增、报告日更晚并关联原报告：真实补发，沿用原编号
    content = csv_of([
        row('07001', 'S701', 'B701', 'P01', 1, '2026-08-02', '<0.01', 0.01),
        row('07001', 'S701', 'B701', 'P01', 2, '2026-08-04', '0.004', 0.001, relation='07001'),
    ])
    s = run(content, {})
    ruling = next(r for r in s['identity_rulings'] if r['id'] == '07001')
    assert ruling['decision'] == '沿用原编号'
    calc = next(c for c in s['calculations'] if c['id'] == '07001')
    assert calc['identity'] == '沿用原编号' and calc['code'] == 'PASS'
    # 原始报告作为版本沿革保留，不丢失
    assert s['lineage'] and s['lineage'][0]['superseded_by'] == '07001'
    assert s['lineage'][0]['version'] == '1'
    assert all(q['code'] != 'IDENTITY' for q in s['quarantine'])


def test_reused_number_different_sample_is_identity_fraud():
    # 同编号但样本/批次不同：编号冒用，偏离行隔离，原始批次证据保留
    content = csv_of([
        row('07002', 'S702', 'B702', 'P01', 1, '2026-08-02', '<0.01', 0.01),
        row('07002', 'S802', 'B802', 'P01', 2, '2026-08-04', '0.004', 0.001),
    ])
    s = run(content, {})
    ruling = next(r for r in s['identity_rulings'] if r['id'] == '07002')
    assert ruling['decision'] == '编号冒用隔离'
    q = [x for x in s['quarantine'] if x['id'] == '07002']
    assert len(q) == 1 and q[0]['code'] == 'IDENTITY'
    # 原始批次 B702 的结论仍以原编号出证，未被另一份报告替换
    calc = next(c for c in s['calculations'] if c['id'] == '07002')
    assert calc['batch'] == 'B702' and calc['sample_id'] == 'S702' and calc['code'] == 'UNCERTAIN'


def test_reused_number_different_analyte_is_identity_fraud():
    # 同编号同样本但检测项目不同（冒用方按补发抬高版本号）：按身份锚点判冒用
    content = csv_of([
        row('07003', 'S703', 'B703', 'P01', 1, '2026-08-02', '0.004', 0.001),
        row('07003', 'S703', 'B703', 'P02', 2, '2026-08-03', '30', 1, method='GC-MS', unit='ug/kg'),
    ])
    s = run(content, {})
    q = [x for x in s['quarantine'] if x['id'] == '07003']
    assert len(q) == 1 and q[0]['code'] == 'IDENTITY'
    calc = next(c for c in s['calculations'] if c['id'] == '07003')
    assert calc['analyte'] == 'P01'


def test_identity_ruling_is_deterministic_on_rerun():
    content = csv_of([
        row('07004', 'S704', 'B704', 'P01', 1, '2026-08-02', '<0.01', 0.01),
        row('07004', 'S904', 'B904', 'P01', 2, '2026-08-04', '0.004', 0.001),
    ])
    a = run(content, {})
    b = run(content, {})
    # 内容寻址 run_id 相同；裁决、结论与证据主体重跑一致（清单时间戳除外）
    assert a['run_id'] == b['run_id']
    assert a['identity_rulings'] == b['identity_rulings']
    assert a['calculations'] == b['calculations']
    assert a['business'] == b['business'] and a['lineage'] == b['lineage']
    assert a['quarantine'] == b['quarantine']


def test_identity_ruling_independent_of_row_order():
    rows = [
        row('07005', 'S705', 'B705', 'P01', 1, '2026-08-02', '<0.01', 0.01),
        row('07005', 'S905', 'B905', 'P01', 2, '2026-08-04', '0.004', 0.001),
    ]
    a = run(csv_of(rows), {})
    b = run(csv_of(list(reversed(rows))), {})
    qa = sorted((q['id'], q['code']) for q in a['quarantine'])
    qb = sorted((q['id'], q['code']) for q in b['quarantine'])
    assert qa == qb and a['business'] == b['business']


def test_fraud_quarantine_does_not_break_reconcile_balance():
    content = csv_of([
        row('07006', 'S706', 'B706', 'P01', 1, '2026-08-02', '<0.01', 0.01),
        row('07006', 'S906', 'B906', 'P01', 2, '2026-08-04', '0.004', 0.001),
        row('07007', 'S707', 'B706', 'P02', 1, '2026-08-02', '30', 1,
            method='GC-MS', unit='ug/kg'),
    ])
    s = run(content, {})
    assert s['reconcile']['balanced'] is True
    grades = {g['batch']: g['code'] for g in s['reconcile']['grades']}
    # B706 含 P02 超限 -> FAIL；冒用方 B906 被隔离，不产生批次等级
    assert grades['B706'] == 'FAIL' and 'B906' not in grades
    assert s['reconcile']['input'] == (s['reconcile']['accepted'] + s['reconcile']['isolated']
                                       + s['reconcile']['blank'] + s['reconcile']['headers']
                                       + s['reconcile']['duplicates'])
