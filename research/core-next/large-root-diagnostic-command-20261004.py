import apsw,json,os
p='/tmp/society0-core-next-20261004/v04-root/current.sqlite'
c=apsw.Connection(p,flags=apsw.SQLITE_OPEN_READONLY)
print(json.dumps({'entry_sizes':list(c.execute('SELECT count(*),sum(raw_bytes),min(raw_bytes),max(raw_bytes),sum(raw_bytes<=256),sum(raw_bytes<=1024),sum(raw_bytes>=65536) FROM fixture_entries')),'chunk_sizes':list(c.execute('SELECT count(*),sum(length(payload)),sum(raw_bytes),min(length(payload)),max(length(payload)) FROM fixture_chunks')),'pages':list(c.execute('SELECT name,count(*),sum(pgsize),sum(payload),sum(unused) FROM dbstat GROUP BY name')),'selection':list(c.execute('SELECT * FROM fixture_selection')),'sqlite':apsw.sqlitelibversion()}))
c.close()
