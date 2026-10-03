"""事实写入事务内维护小型累计投影；共享批次的主体归属保持显式。"""
TIMINGS=('duration_s','queue_s','jitter_s','provider_s')
COUNTERS=('requests','responses','errors','input_tokens','output_tokens','total_tokens',
          'input_reports','output_reports','total_reports','cancelled','decode_errors','embedding_uses')+TIMINGS+tuple(name.removesuffix('_s')+'_reports' for name in TIMINGS)


def schema(prefix):
    columns=','.join(f'{name} {"REAL" if name in TIMINGS else "INTEGER"} NOT NULL' for name in COUNTERS)
    return (
        f'CREATE TABLE {prefix}_usage_calls(id TEXT PRIMARY KEY NOT NULL,model TEXT NOT NULL,{columns})',
        f'CREATE TABLE {prefix}_usage_actors(call_id TEXT NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(call_id,actor))',
        f'CREATE TABLE {prefix}_usage_totals(scope INTEGER NOT NULL,actor TEXT NOT NULL,model TEXT NOT NULL,{columns},PRIMARY KEY(scope,actor,model))',
    )


def _add(writer,prefix,actor,model,values):
    scope=int(actor is not None)
    updates=','.join(f'{name}={name}+excluded.{name}' for name in COUNTERS)
    writer.execute(f'INSERT INTO {prefix}_usage_totals VALUES({",".join("?" for _ in range(3+len(COUNTERS)))}) '
                   f'ON CONFLICT(scope,actor,model) DO UPDATE SET {updates}',
                   (scope,actor or '',model,*values))


def begin(writer,prefix,identifier,model,actor=None):
    values=(1,)+(0,)*(len(COUNTERS)-1)
    writer.execute(f'INSERT INTO {prefix}_usage_calls VALUES({",".join("?" for _ in range(2+len(COUNTERS)))})',
                   (identifier,model,*values))
    _add(writer,prefix,None,model,values)
    if actor is not None:link(writer,prefix,identifier,actor)


def link(writer,prefix,identifier,actor):
    if writer.query(f'SELECT 1 FROM {prefix}_usage_actors WHERE call_id=? AND actor=?',(identifier,actor)):
        return
    row=writer.query(f'SELECT model,{",".join(COUNTERS)} FROM {prefix}_usage_calls WHERE id=?',(identifier,))[0]
    writer.execute(f'INSERT INTO {prefix}_usage_actors VALUES(?,?)',(identifier,actor))
    _add(writer,prefix,actor,row[0],row[1:])


def finish(writer,prefix,identifier,*,outcome,body,timing=None):
    rows=writer.query(f'SELECT model,{",".join(COUNTERS)} FROM {prefix}_usage_calls WHERE id=?',(identifier,))
    if not rows:raise KeyError('physical request not found')
    row=rows[0]
    model,previous=row[0],dict(zip(COUNTERS,row[1:]))
    usage=body.get('usage',{}) if isinstance(body,dict) else {}
    usage=usage if isinstance(usage,dict) else {}
    values=dict.fromkeys(COUNTERS,0)
    if outcome in ('response','decode_error'):values['responses']=1
    if outcome in ('error','decode_error'):values['errors']=1
    if outcome=='decode_error':values['decode_errors']=1
    if outcome=='cancelled':values['cancelled']=1
    for source,target in (('prompt_tokens','input'),('completion_tokens','output'),('total_tokens','total')):
        value=usage.get(source)
        if type(value) is int and value>=0 and not previous[target+'_reports']:
            values[target+'_tokens']=value
            values[target+'_reports']=1
    for name in TIMINGS:
        report=name.removesuffix('_s')+'_reports'
        value=(timing or {}).get(name)
        if type(value) in (int,float) and not previous[report]:
            values[name]=value;values[report]=1
    # 一个响应可先成功留证，再暴露解码/后处理故障；用量只记第一次报告。
    delta=tuple(values[name] if name.endswith('_tokens') or name.endswith('_reports') or name in TIMINGS
                else max(0,values[name]-previous[name]) for name in COUNTERS)
    writer.execute(f'UPDATE {prefix}_usage_calls SET '+','.join(f'{name}={name}+?' for name in COUNTERS)+' WHERE id=?',(*delta,identifier))
    _add(writer,prefix,None,model,delta)
    for (actor,) in writer.iter_query(f'SELECT actor FROM {prefix}_usage_actors WHERE call_id=?',(identifier,)):
        _add(writer,prefix,actor,model,delta)


def logical_embedding(writer,model,payload):
    actor=payload.get('metadata',{}).get('actor')
    values=tuple(int(name=='embedding_uses') for name in COUNTERS)
    _add(writer,'resource',None,model,values)
    if actor is not None:
        _add(writer,'resource',actor,model,values)
        for identifier in dict.fromkeys(item['call_id'] for item in payload['sources']):
            link(writer,'resource',identifier,actor)


def read(view,*,actor=None,model=None):
    totals=dict.fromkeys(COUNTERS,0);items=[]
    for prefix,kind in (('thread','llm'),('resource','embedding')):
        if not view.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(prefix+'_usage_totals',)):
            continue
        sql=f'SELECT model,{",".join(COUNTERS)} FROM {prefix}_usage_totals WHERE scope=? AND actor=?'
        parameters=[int(actor is not None),actor or '']
        if model is not None:sql+=' AND model=?';parameters.append(model)
        for row in view.iter_query(sql+' ORDER BY model',parameters):
            counts=dict(zip(COUNTERS,row[1:]))
            items.append({'kind':kind,'model':row[0],**counts,'unknown_usage_calls':counts['requests']-counts['total_reports']})
            for name,value in counts.items():totals[name]+=value
    totals['unknown_usage_calls']=totals['requests']-totals['total_reports']
    return {'actor':actor,'timing_scope':'sum_of_physical_attempt_wall_times','attribution':'physical_calls_once' if actor is None else 'related_physical_calls_not_additive',
            'totals':totals,'models':items}
