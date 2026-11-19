from collections import defaultdict
from decimal import Decimal
from app.reasons import IDENTITY_ANCHOR

SHARED = ('sample_id', 'batch', 'product', 'analyte', 'method', 'sampled')


def anchor_of(r):
    # 身份锚点：同一检测编号只能归属同一组样本/批次/产品/残留项目
    return tuple(r[k] for k in IDENTITY_ANCHOR)


def _identity_rulings(reports):
    """按编号裁决补发是否可沿用原编号。

    同编号只有一个身份锚点 -> 真实补发，沿用原编号；
    同编号出现多个锚点 -> 以原始报告（无 relation，否则最早报告日/版本）锚点为准，
    偏离锚点的报告判编号冒用并按行隔离，原始报告保留。
    """
    by_id = defaultdict(list)
    for r in reports:
        by_id[r['id']].append(r)
    kept, impostor_lines, rulings = [], set(), []
    for rid in sorted(by_id):
        rows = by_id[rid]
        anchors = {anchor_of(r) for r in rows}
        if len(anchors) == 1:
            kept.extend(rows)
            rulings.append({'id': rid, 'decision': '沿用原编号',
                            'anchor': list(anchors)[0],
                            'versions': sorted({str(r['version']) for r in rows}),
                            'lines': sorted(r['_line'] for r in rows)})
            continue
        roots = [r for r in rows if not r.get('relation')]
        base = sorted(roots or rows,
                      key=lambda r: (r['reported'], Decimal(str(r['version'])), r['_line']))[0]
        rulings.append({'id': rid, 'decision': '编号冒用隔离',
                        'anchor': list(anchor_of(base)),
                        'versions': sorted({str(r['version']) for r in rows}),
                        'lines': sorted(r['_line'] for r in rows)})
        for r in rows:
            if anchor_of(r) == anchor_of(base):
                kept.append(r)
            else:
                impostor_lines.add(r['_line'])
    return kept, impostor_lines, rulings


def select_reports(reports):
    """返回 (采纳报告, {报告id: 原因码}, 编号裁决, 版本沿革, 隔离行原因)。"""
    kept, impostor_lines, rulings = _identity_rulings(reports)
    bad_lines = {line: 'IDENTITY' for line in impostor_lines}
    # 同编号补发存在多个 (id, version)：异编号复检按 id 解析，同编号补发按版本解析
    line_of = {r['_line']: r for r in kept}
    by_id = {}
    id_versions = defaultdict(dict)
    for r in kept:
        by_id.setdefault(r['id'], r)
        id_versions[r['id']].setdefault(str(r['version']), r)

    def resolve(current):
        if current['id'] == current['relation']:  # 同编号补发：指向自身旧版本
            versions = id_versions.get(current['id'], {})
            older = [v for v in versions.values()
                     if Decimal(str(v['version'])) < Decimal(str(current['version']))]
            return max(older, key=lambda x: Decimal(str(x['version']))) if older else None
        return by_id.get(current['relation'])

    def mark_line(r, code):
        bad_lines[r['_line']] = code

    def validate_chain(start):
        """遍历一条 relation 链，返回 (隔离行集合, 环成员行集合)。"""
        faults, cycle_members = set(), set()
        chain, seen = [], {}
        current = start
        while current.get('relation'):
            seen[current['_line']] = len(chain)
            chain.append(current['_line'])
            parent = resolve(current)
            if parent is None:  # 悬空引用
                faults.add(current['_line'])
                return faults, cycle_members
            if parent['_line'] in seen:  # 引用环：环判定优先于边校验，环上节点全部隔离
                loop = chain[seen[parent['_line']]:]
                cycle_members.update(loop)
                faults.update(loop)
                return faults, cycle_members
            same_id = current['id'] == parent['id']
            # 复检（异编号）要求方法、采样日等全字段一致；同编号补发只校验身份锚点，
            # 并以版本递增、报告日严格更晚证明它是后发的补发件
            shared = IDENTITY_ANCHOR if same_id else SHARED
            if any(current[k] != parent[k] for k in shared) or current['reported'] <= parent['reported']:
                faults.add(current['_line'])
                return faults, cycle_members
            if same_id and Decimal(str(current['version'])) <= Decimal(str(parent['version'])):
                faults.add(current['_line'])
                return faults, cycle_members
            current = parent
        return faults, cycle_members

    # A relation is a replacement chain; walk every edge, detecting cycles,
    # dangling references, cross-identity links and non-strict report dates.
    # 同编号补发时 id 会重复，因此用行号判环，不能用报告编号。
    for r in kept:
        faults, _ = validate_chain(r)
        for line in faults:
            mark_line(line_of[line], 'REFERENCE')

    # 按身份锚点分组：同编号多版本（真实补发）与复检链都落在同一组内裁决
    grouped = defaultdict(list)
    for r in kept:
        if r['_line'] not in bad_lines:
            grouped[anchor_of(r)].append(r)

    valid, lineage = [], []
    for key in sorted(grouped):
        rows = grouped[key]
        # 每个报告编号只允许一个原始根；复检 relation 链上的子报告不计根
        roots_by_id = defaultdict(list)
        for r in rows:
            if not r['relation']:
                roots_by_id[r['id']].append(r)
        if any(len(v) != 1 for v in roots_by_id.values()):
            for r in rows:
                mark_line(r, 'CONFLICT')
            continue
        rank = max((r['reported'], Decimal(str(r['version']))) for r in rows)
        newest = [r for r in rows if (r['reported'], Decimal(str(r['version']))) == rank]
        if len({(r['lower'], r['upper']) for r in newest}) > 1:  # 并列最新且结论矛盾
            for r in rows:
                mark_line(r, 'CONFLICT')
            continue
        chosen = sorted(newest, key=lambda x: x['id'])[0]
        valid.append(chosen)
        # 历史版本留作采信证据：被替代的原始/旧版报告全部入册，原始报告不丢失
        for r in sorted(rows, key=lambda x: (x['reported'], Decimal(str(x['version'])), x['_line'])):
            if r['id'] == chosen['id'] and r['version'] == chosen['version']:
                continue
            lineage.append({'id': r['id'], 'line': r['_line'], 'sample_id': r['sample_id'],
                            'batch': r['batch'], 'analyte': r['analyte'], 'version': str(r['version']),
                            'reported': r['reported'], 'lower': r['lower'], 'upper': r['upper'],
                            'code': r['code'], 'superseded_by': chosen['id']})

    bad_by_id = {r['id']: bad_lines[r['_line']] for r in reports if r['_line'] in bad_lines}
    return valid, bad_by_id, rulings, lineage, bad_lines


def apply(state):
    chosen, bad, rulings, lineage, bad_lines = select_reports(state['measurements'])
    for r in state['measurements']:
        if r['_line'] in bad_lines:
            state['quarantine'].append({'line': r['_line'], 'id': r['id'],
                                        'code': bad_lines[r['_line']], 'raw': r['_raw']})
    state['rows'] = [r for r in state['rows'] if r['_line'] not in bad_lines]
    calculations = [{'id': r['id'], 'line': r['_line'], 'sample_id': r['sample_id'], 'batch': r['batch'],
                     'analyte': r['analyte'], 'method': r['method'], 'standard': r['standard_id'],
                     'lower': r['lower'], 'upper': r['upper'], 'upper_exclusive': r['upper_exclusive'],
                     'limit': r['limit'], 'code': r['code'], 'replaces': r['relation'],
                     'identity': '沿用原编号'} for r in chosen]
    state['calculations'] = calculations
    state['identity_rulings'] = rulings
    state['lineage'] = lineage
    state['business'] = {'total': len(calculations),
                         'pass': sum(r['code'] == 'PASS' for r in calculations),
                         'fail': sum(r['code'] == 'FAIL' for r in calculations),
                         'uncertain': sum(r['code'] == 'UNCERTAIN' for r in calculations),
                         'unit': '检测项目'}
    return state
