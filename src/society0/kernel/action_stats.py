"""LLM 工具执行的短投影；原始参数、结果和错误由 Thread 保存。"""
SCHEMA=(
    'CREATE TABLE thread_action_calls(id INTEGER PRIMARY KEY,thread_id TEXT NOT NULL,start_seq INTEGER NOT NULL,actor TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,finish_seq INTEGER,elapsed_s REAL,failed INTEGER NOT NULL,UNIQUE(thread_id,start_seq))',
    'CREATE INDEX thread_action_errors ON thread_action_calls(failed,id)',
    'CREATE INDEX thread_actor_action_errors ON thread_action_calls(actor,failed,id)',
    'CREATE TABLE thread_action_totals(scope INTEGER NOT NULL,actor TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,count INTEGER NOT NULL,total_s REAL NOT NULL,max_s REAL NOT NULL,PRIMARY KEY(scope,actor,name,status))',
    'CREATE TABLE thread_action_tags(scope INTEGER NOT NULL,actor TEXT NOT NULL,tag TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(scope,actor,tag))',
    'CREATE TABLE thread_activation_phases(scope INTEGER NOT NULL,actor TEXT NOT NULL,phase TEXT NOT NULL,count INTEGER NOT NULL,total_s REAL NOT NULL,max_s REAL NOT NULL,PRIMARY KEY(scope,actor,phase))',
    'CREATE TABLE thread_activation_totals(scope INTEGER NOT NULL,actor TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,count INTEGER NOT NULL,total_s REAL NOT NULL,max_s REAL NOT NULL,PRIMARY KEY(scope,actor,status,reason))',
)


def _scopes(actor):return ((0,''),(1,actor))


def _increment(writer,table,keys,elapsed_s):
    writer.execute(f'INSERT INTO {table} VALUES(?,?,?,?,1,?,?) ON CONFLICT DO UPDATE SET '
                   'count=count+1,total_s=total_s+excluded.total_s,max_s=MAX(max_s,excluded.max_s)',
                   (*keys,elapsed_s,elapsed_s))


def start(writer,thread_id,sequence,actor,name):
    writer.execute('INSERT INTO thread_action_calls(thread_id,start_seq,actor,name,status,failed) VALUES(?,?,?,?,?,0)',
                   (thread_id,sequence,actor,name,'started'))
    for scope,owner in _scopes(actor):_increment(writer,'thread_action_totals',(scope,owner,name,'started'),0.)


def finish(writer,thread_id,sequence,finish_seq,status,tags,elapsed_s):
    if status not in ('completed','accepted','rejected','error','cancelled'):
        raise ValueError('invalid action outcome')
    rows=writer.query('SELECT actor,name,status FROM thread_action_calls WHERE thread_id=? AND start_seq=?',(thread_id,sequence))
    if not rows:raise KeyError('action start not found')
    actor,name,previous=rows[0]
    if previous!='started':raise ValueError('action outcome already recorded')
    failed=int(status in ('rejected','error','cancelled'))
    writer.execute('UPDATE thread_action_calls SET status=?,finish_seq=?,elapsed_s=?,failed=? WHERE thread_id=? AND start_seq=?',
                   (status,finish_seq,elapsed_s,failed,thread_id,sequence))
    for scope,owner in _scopes(actor):
        _increment(writer,'thread_action_totals',(scope,owner,name,status),elapsed_s)
        if status=='completed':
            for tag in dict.fromkeys(tags):
                writer.execute('INSERT INTO thread_action_tags VALUES(?,?,?,1) ON CONFLICT DO UPDATE SET count=count+1',(scope,owner,tag))


def activation(writer,actor,status,reason,elapsed_s,timings):
    for scope,owner in _scopes(actor):
        _increment(writer,'thread_activation_totals',(scope,owner,status,reason),elapsed_s)
        for name,duration in (timings or {}).items():
            writer.execute('INSERT INTO thread_activation_phases VALUES(?,?,?,1,?,?) ON CONFLICT DO UPDATE SET '
                           'count=count+1,total_s=total_s+excluded.total_s,max_s=MAX(max_s,excluded.max_s)',
                           (scope,owner,name,duration,duration))


def read(view,*,actor=None,error_limit=5):
    scope,owner=int(actor is not None),actor or ''
    result={'scope':'llm_tool_actions','actor':actor,'action_counts':{},'successful_action_counts':{},
            'failed_action_counts':{},'accepted_action_counts':{},'action_tag_counts':{},
            'action_duration_summary':{},'error_samples':[],'activations':[],'phase_timings':{}}
    for name,status,count,total_s,max_s in view.iter_query('SELECT name,status,count,total_s,max_s FROM thread_action_totals WHERE scope=? AND actor=? ORDER BY name,status',(scope,owner)):
        key={'started':'action_counts','completed':'successful_action_counts','accepted':'accepted_action_counts'}.get(status,'failed_action_counts')
        result[key][name]=result[key].get(name,0)+count
        if status!='started':
            times=result['action_duration_summary'].setdefault(name,{'count':0,'total_s':0.,'max_s':0.})
            times['count']+=count;times['total_s']+=total_s;times['max_s']=max(times['max_s'],max_s)
    result['action_tag_counts']=dict(view.query('SELECT tag,count FROM thread_action_tags WHERE scope=? AND actor=? ORDER BY tag',(scope,owner)))
    for status,reason,count,total_s,max_s in view.iter_query('SELECT status,reason,count,total_s,max_s FROM thread_activation_totals WHERE scope=? AND actor=? ORDER BY status,reason',(scope,owner)):
        result['activations'].append(dict(status=status,reason=reason,count=count,total_s=total_s,max_s=max_s))
    result['phase_timings']={name:dict(count=count,total_s=total_s,max_s=max_s) for name,count,total_s,max_s in view.iter_query('SELECT phase,count,total_s,max_s FROM thread_activation_phases WHERE scope=? AND actor=? ORDER BY phase',(scope,owner))}
    sql='SELECT thread_id,start_seq,finish_seq,name,status,elapsed_s FROM thread_action_calls WHERE failed=1'
    parameters=[]
    if actor is not None:sql+=' AND actor=?';parameters.append(actor)
    parameters.append(error_limit)
    result['error_samples']=[dict(zip(('thread_id','start_seq','finish_seq','name','status','elapsed_s'),row))
        for row in view.iter_query(sql+' ORDER BY id DESC LIMIT ?',parameters)]
    return result
