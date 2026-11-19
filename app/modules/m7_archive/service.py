from app.modules.m7_archive.export_out import checksum

def execute(state):
    state['archive']={'sha256':checksum(state),
        'evidence_count':len(state.get('calculations',[]))+len(state['quarantine'])
            +len(state.get('lineage',[]))+len(state.get('identity_rulings',[])),
        'identity_kept':sum(1 for r in state.get('identity_rulings',[]) if r['decision']=='沿用原编号'),
        'identity_rejected':sum(1 for r in state.get('identity_rulings',[]) if r['decision']=='编号冒用隔离'),
        'ready':state.get('reconcile',{}).get('balanced',False)}
    return state
