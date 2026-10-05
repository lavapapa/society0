"""真实根规模试验先以小输入核验原文、范围读取和恢复。"""
from benchmarks.core_next_large_root_probe import build_fixture, load_entry, read_range, modify_probe, disk_usage
from society0.kernel.storage import StageStore


def test_fixture_keeps_order_types_ranges_and_selected_complete(tmp_path):
    entries=[{'path':['m',True,1,'1',None],'op':'set','value':{'text':'甲\\"😀'*40000}},
             {'path':['m','small'],'op':'set','value':{'count':7}},
             {'path':['empty'],'op':'set','value':[]}]
    with build_fixture(tmp_path/'run',iter(entries)) as store:
        assert [load_entry(store,i) for i in range(3)]==entries
        raw=read_range(store,0,65000,100)
        import json
        expected=json.dumps(entries[0],ensure_ascii=False,separators=(',',':')).encode()
        assert raw==expected[65000:65100]
        result=modify_probe(store,[0],mode='split',steps=2)
        assert [x['step'] for x in result]==[1,2]
        assert load_entry(store,0)==entries[0]
        store.transaction(lambda w:w.execute('UPDATE fixture_hot SET value=99 WHERE seq=0'))
    with StageStore.restore(tmp_path/'run',tmp_path/'restored',step=2) as restored:
        assert restored.read(lambda r:r.query('SELECT value FROM fixture_hot WHERE seq=0'))==[(2,)]
        assert load_entry(restored,0)==entries[0]
        assert disk_usage(restored.path)['logical_bytes']>0


def test_whole_record_edit_preserves_other_values_and_measures_complete(tmp_path):
    entry={'path':['x'],'op':'set','value':{'cold':'x'*200000,'balance':3}}
    with build_fixture(tmp_path/'run',[entry]) as store:
        results=modify_probe(store,[0],mode='whole',steps=2)
        actual=load_entry(store,0)
        assert actual['value']['cold']==entry['value']['cold']
        assert actual['value']['balance']==3
        assert actual['value']['_probe_hot']==2
        assert all(r['changeset_bytes']>0 for r in results)
        assert all(r['session_bytes_before_complete']>0 for r in results)


def test_ten_mib_cold_record_exposes_full_rewrite_cost(tmp_path):
    entry={'path':['large'],'operation':'set','value':{'cold':'x'*(10*1024*1024)}}
    with build_fixture(tmp_path/'whole',[entry]) as store:
        whole=modify_probe(store,[0],mode='whole',steps=1)[0]
        expected={'path':['large'],'operation':'set','value':{'cold':entry['value']['cold'],'_probe_hot':1}}
        assert load_entry(store,0)==expected
    with StageStore.restore(tmp_path/'whole',tmp_path/'whole-restored') as restored:
        assert restored.complete_step==1 and load_entry(restored,0)==expected
    with build_fixture(tmp_path/'split',[entry]) as store:
        split=modify_probe(store,[0],mode='split',steps=1)[0]
        assert load_entry(store,0)==entry
    # 原生 Session 折叠重插入的相同块；末尾小改无需输出全部正文。
    assert whole['changeset_bytes']>split['changeset_bytes']
    assert whole['changeset_bytes']<1024
    assert whole['session_bytes_before_complete']>split['session_bytes_before_complete']
    small={'path':['large'],'operation':'set','value':{'cold':'x'*(1024*1024)}}
    with build_fixture(tmp_path/'small',[small]) as store:
        small_cost=modify_probe(store,[0],mode='whole',steps=1)[0]
    assert whole['session_bytes_before_complete']>small_cost['session_bytes_before_complete']
    assert whole['changeset_bytes']<1024
    assert whole['before_marker_disk']['logical_bytes']>=whole['live_disk']['logical_bytes']


def test_alignment_probe_compares_three_positions_without_assumed_ratio(tmp_path):
    from benchmarks.core_next_large_root_probe import alignment_probe
    results=alignment_probe(tmp_path,body_bytes=256*1024)
    assert [r['case'] for r in results]==['tail_length_change','front_equal_length','front_length_change']
    assert all(r['all_values_equal'] for r in results)
    assert all(r['changeset_bytes']>0 for r in results)
