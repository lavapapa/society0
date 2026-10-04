"""显式主体分析与批结果读取；常用角色选择复用权威索引。"""
import random
import statistics


def select_ids(actors, *, role=None, active=True, predicate=None):
    """按登记顺序遍历选择范围；任意 predicate 逐主体读取所需字段。"""
    cursor = None
    while True:
        page = actors.select(role=role, active=active, cursor=cursor)
        for identifier in page.items:
            if predicate is None or predicate(actors.view(identifier)):
                yield identifier
        cursor = page.next_cursor
        if cursor is None:
            return


def sample_ids(identifiers, count, *, seed=None):
    """流式 reservoir 抽样；结果按输入顺序返回，驻留量随样本数增长。"""
    if type(count) is not int or count < 0:
        raise ValueError('sample count must be a nonnegative integer')
    if count == 0:
        return []
    randomizer = random.Random(seed)
    selected = []
    for position, identifier in enumerate(identifiers):
        if position < count:
            selected.append((position, identifier))
        else:
            slot = randomizer.randrange(position + 1)
            if slot < count:
                selected[slot] = (position, identifier)
    selected.sort(key=lambda item: item[0])
    return [identifier for _, identifier in selected]


def result_rows(results):
    """逐行保留实际主体轮次与完整返回值，领域字段不会覆盖身份。"""
    for record in results:
        yield {'actor_id': record.actor_id, 'round': record.round,
               'status': record.result.status, 'reason': record.result.reason,
               'value': record.result.value}


def result_values(results, field):
    path = (field,) if isinstance(field, str) else tuple(field)
    for record in results:
        value = record.result.value
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            yield value


def result_mean(results, field):
    values = (value for value in result_values(results, field)
              if isinstance(value, (int, float)))
    try:
        return statistics.fmean(values)
    except statistics.StatisticsError:
        return None
