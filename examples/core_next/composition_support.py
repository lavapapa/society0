"""组合示例的持久通知：范围读取与保存后回执，共享原生存储。"""
from society0.kernel.information_sql import _quote
from society0.kernel.plugins import Plugin
from society0.plugins.cognition import SourceBatch


class Notifications:
    def __init__(self, store, domain):
        self.store, self.domain = store, domain

    def table(self, suffix):
        return _quote(self.domain + '_' + suffix)

    def publish(self, actor, body):
        def write(writer):
            writer.execute(f'INSERT INTO {self.table("notices")}(actor,body) VALUES(?,?)', (actor, body.encode()))
            return writer.query('SELECT last_insert_rowid()')[0][0]
        return self.store.transaction(write)

    def since(self, actor, after):
        return self.store.read(lambda view: [(identifier, body.decode()) for identifier, body in view.iter_query(
            f'SELECT id,body FROM {self.table("notices")} WHERE actor=? AND id>? ORDER BY id', (actor, after))])

    def acknowledge(self, actor, thread_id, notice_ids):
        self.store.transaction(lambda writer: writer.executemany(
            f'INSERT INTO {self.table("receipts")} VALUES(?,?,?)',
            ((actor, thread_id, identifier) for identifier in notice_ids)))

    def receipts(self, actor):
        return self.store.read(lambda view: view.query(
            f'SELECT thread_id,notice_id FROM {self.table("receipts")} WHERE actor=? ORDER BY thread_id,notice_id', (actor,)))


class NotificationSource:
    def __init__(self, notifications):
        self.notifications = notifications

    def __call__(self, session, previous):
        actor = session.actor.id
        after = 0 if previous is None else previous.position
        rows = self.notifications.since(actor, after)
        messages = [{'role': 'user', 'content': f'通知 {self.notifications.domain}/{identifier}\n{body}'}
                    for identifier, body in rows]
        def confirm(context):
            context.session.scope.check_active()
            self.notifications.acknowledge(actor, context.thread_id, [identifier for identifier, body in rows])
        return SourceBatch(messages, rows[-1][0] if rows else after, effects=(confirm,) if rows else ())


def notification_data_plugin(*, name='notice.data', storage='storage', actors='actors.data'):
    table = lambda suffix: _quote(name + '_' + suffix)
    schema = (
        f'CREATE TABLE {table("notices")}(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,body BLOB NOT NULL,FOREIGN KEY(actor) REFERENCES actors(id))',
        f'CREATE INDEX {table("actor_notices")} ON {table("notices")}(actor,id)',
        f'CREATE TABLE {table("receipts")}(actor TEXT NOT NULL,thread_id TEXT NOT NULL,notice_id INTEGER NOT NULL,PRIMARY KEY(actor,thread_id,notice_id),FOREIGN KEY(actor) REFERENCES actors(id),FOREIGN KEY(notice_id) REFERENCES {table("notices")}(id))',
    )
    def install(ctx):
        ctx.provide('notifications', Notifications(ctx.require(storage, 'store'), name))
    return Plugin(name, requires=(storage,), schema_requires=(actors,), schema=schema, install=install)


def notification_source_plugin(*, name='notice.source', data=('notice.data', 'notifications')):
    def install(ctx):
        ctx.provide('source', NotificationSource(ctx.require(*data)))
    return Plugin(name, requires=(data[0],), install=install)
