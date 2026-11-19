import csv, io
from app import store
from app.modules.m7_archive.export_out import bundle, checksum
from app.workflow import load, run
from app.errors import DomainError
import pytest

BOM = '﻿'
HEADER = 'id,sample_id,batch,product,analyte,method,version,sampled,reported,result,unit,lod,relation,note'


def _row(rid, sample, batch, analyte, version, reported, result, lod,
         relation='', method='LC-MS/MS', unit='mg/kg'):
    return ','.join([rid, sample, batch, '苹果', analyte, method, str(version),
                     '2026-08-01', reported, result, unit, str(lod), relation, '测试'])


def _reissue_csv():
    return (HEADER + '\n' + '\n'.join([
        _row('08001', 'S801', 'B801', 'P01', 1, '2026-08-02', '<0.01', 0.01),
        _row('08001', 'S801', 'B801', 'P01', 2, '2026-08-04', '0.004', 0.001, relation='08001'),
        _row('08002', 'S802', 'B802', 'P01', 1, '2026-08-02', '<0.01', 0.01),
        _row('08002', 'S902', 'B902', 'P01', 2, '2026-08-04', '0.004', 0.001),
    ]) + '\n').encode()


def test_export_rows(state):
    rows = list(csv.reader(io.StringIO(bundle(state).lstrip(BOM))))
    assert rows[0][0] == '记录类型' and rows[1][0] == '运行清单'


def test_export_quarantine(state):
    assert bundle(state).count('隔离') == len(state['quarantine'])


def test_checksum_stable(state):
    assert checksum(state) == state['archive']['sha256'] and len(checksum(state)) == 64


def test_archive_ready(state):
    assert state['archive']['ready'] is True


def test_idempotent_store(state, tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    a = store.save(state['run_id'], state)
    b = store.save(state['run_id'], state)
    assert a['run_id'] == b['run_id'] and len(store.history()) == 1


def test_stale_review(state, tmp_path, monkeypatch):
    # TRAP-26
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    store.save(state['run_id'], state)
    assert store.review(state['run_id'], 1, '通过')['review_version'] == 2
    with pytest.raises(DomainError) as e:
        store.review(state['run_id'], 1, '退回')
    assert e.value.status == 409


def test_persistent_new_connection(state, tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    store.save(state['run_id'], state)
    assert store.get(state['run_id'])['calculations'] == state['calculations']


def test_bundle_records_ruling_lineage_and_fraud():
    s = run(_reissue_csv(), {})
    text = bundle(s)
    assert '编号裁决' in text and '沿用原编号' in text and '编号冒用隔离' in text
    rows = [r for r in csv.reader(io.StringIO(text.lstrip(BOM))) if r]
    # 原始报告作为版本沿革进入封存证据，记录不丢失
    lineage_rows = [r for r in rows if r[0] == '版本沿革']
    assert lineage_rows and lineage_rows[0][3] == '08001'
    # 冒用件进入隔离签，且原始批次仍在计算证据中
    fraud = [r for r in rows if r[0] == '隔离']
    assert any(r[3] == '08002' and r[4] == 'IDENTITY' for r in fraud)
    calcs = [r for r in rows if r[0] == '计算']
    assert any(r[3] == '08002' for r in calcs)


def test_archive_counts_cover_all_evidence():
    s = run(_reissue_csv(), {})
    expected = len(s['calculations']) + len(s['quarantine']) + len(s['lineage']) + len(s['identity_rulings'])
    assert s['archive']['evidence_count'] == expected
    assert s['archive']['identity_kept'] == 1 and s['archive']['identity_rejected'] == 1


def test_rerun_does_not_duplicate_identity_registry(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    s = run(_reissue_csv(), {})
    store.save(s['run_id'], s)
    store.save(s['run_id'], s)  # 重跑同一内容
    registry = store.identity_registry('08001')
    assert len(registry) == 1 and registry[0]['decision'] == '沿用原编号'
    fraud = store.identity_registry('08002')
    assert {r['decision'] for r in fraud} == {'沿用原编号', '编号冒用隔离'}
    assert len(store.history()) == 1  # 不重复出证


def test_registry_keeps_old_batch_anchor_after_fraud(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    s = run(_reissue_csv(), {})
    store.save(s['run_id'], s)
    rows = store.identity_registry('08002')
    kept = next(r for r in rows if r['decision'] == '沿用原编号')
    assert kept['anchor'][1] == 'B802'  # 旧批次锚点保留，未被 B902 替换
    rejected = next(r for r in rows if r['decision'] == '编号冒用隔离')
    assert rejected['anchor'][1] == 'B902'


def test_identity_endpoint_served(client):
    r = client.post('/api/runs', json={'sample': 'dirty.csv'})
    assert r.status_code == 200
    listing = client.get('/api/identities')
    assert listing.status_code == 200 and isinstance(listing.get_json(), list)
