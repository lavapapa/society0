"""从行动 schema 生成参数发现与纠错说明；业务状态由领域结果提供。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from dataclasses import asdict, is_dataclass
import json

from jsonschema.validators import validator_for


_MISSING = object()


def required_parameters(schema):
    properties = schema.get('properties', {})
    def resolve(spec):
        if '$ref' in spec and spec['$ref'].startswith('#/'):
            result = schema
            for key in spec['$ref'][2:].split('/'):
                result = result[key.replace('~1', '/').replace('~0', '~')]
            return resolve(result)
        return parameter_type(spec)
    return {name: resolve(properties.get(name, {})) for name in schema.get('required', ())}


def parameter_type(schema):
    if '$ref' in schema:
        return schema['$ref']
    if 'type' in schema:
        return schema['type']
    if 'enum' in schema:
        return 'enum: ' + json.dumps(schema['enum'], ensure_ascii=False)
    return [parameter_type(item) for item in schema.get('anyOf', schema.get('oneOf', ()))] or 'any'


def call_example(schema, arguments=None, available_ids=None):
    """仅发布通过完整 schema 验证的示例；缺少来源的对象 ID 留待先查询。"""
    root = schema

    def build(spec, supplied=_MISSING, name=''):
        if '$ref' in spec:
            if not spec['$ref'].startswith('#/'):
                return _MISSING
            resolved = root
            for key in spec['$ref'][2:].split('/'):
                resolved = resolved[key.replace('~1', '/').replace('~0', '~')]
            return build(resolved, supplied, name)
        validator = validator_for(root)(root).evolve(schema=spec)
        available = _MISSING
        if isinstance(available_ids, dict):
            keys = (name, name[:-1]) if name.endswith('_ids') else (name, name + 's') if name.endswith('_id') else (name,)
            available = next((available_ids[key] for key in keys if key in available_ids), _MISSING)
        elif available_ids is not None and (name == 'id' or name.endswith('_id')):
            available = available_ids
        if available is not _MISSING:
            values = available if isinstance(available, list) else [available]
            kind = spec.get('type')
            array = (kind == 'array' or isinstance(kind, list) and 'array' in kind
                     or any(branch.get('type') == 'array' for branch in spec.get('anyOf', spec.get('oneOf', ()))))
            replacements = [values[:spec.get('maxItems', len(values))], *values] if array else values
            for candidate in replacements:
                if validator.is_valid(candidate):
                    return deepcopy(candidate)
            return _MISSING
        candidates = [spec[key] for key in ('example', 'default') if key in spec]
        candidates.extend(spec.get('examples', ()))
        if 'const' in spec:
            candidates.append(spec['const'])
        candidates.extend(spec.get('enum', ()))
        if supplied is not _MISSING:
            candidates.append(supplied)
        for candidate in candidates:
            if validator.is_valid(candidate):
                if available_ids is not None and isinstance(candidate, (dict, list)):
                    supplied = candidate
                    break
                return deepcopy(candidate)
        for branch in spec.get('anyOf', spec.get('oneOf', ())):
            candidate = build(branch, supplied, name)
            if candidate is not _MISSING and validator.is_valid(candidate):
                return candidate
        kind = spec.get('type')
        if isinstance(kind, list):
            kind = next((item for item in kind if item != 'null'), 'null')
        if kind == 'object' or 'properties' in spec:
            candidate = {}
            source = supplied if isinstance(supplied, dict) else {}
            for key, child in spec.get('properties', {}).items():
                known = isinstance(available_ids, dict) and (key in available_ids
                    or key.endswith('_ids') and key[:-1] in available_ids
                    or key.endswith('_id') and key + 's' in available_ids)
                if key not in spec.get('required', ()) and key not in source and not known:
                    continue
                value = build(child, source.get(key, _MISSING), key)
                if value is _MISSING:
                    if key in spec.get('required', ()):
                        return _MISSING
                    continue
                candidate[key] = value
        elif kind == 'array':
            candidate = []
            source = supplied if isinstance(supplied, list) else []
            for index in range(max(spec.get('minItems', 0), len(source))):
                value = build(spec.get('items', {}), source[index] if index < len(source) else _MISSING)
                if value is _MISSING:
                    return _MISSING
                candidate.append(value)
        elif name == 'id' or name.endswith(('_id', '_ids')):
            return _MISSING
        elif kind in ('integer', 'number'):
            candidate = max(spec.get('minimum', 0), spec.get('exclusiveMinimum', -1) + 1)
            if kind == 'integer':
                import math
                candidate = math.ceil(candidate)
        elif kind == 'boolean':
            candidate = False
        elif kind == 'null':
            candidate = None
        elif kind == 'string':
            candidate = date.today().isoformat() if spec.get('format') == 'date' else '请填写'
            candidate = candidate.ljust(spec.get('minLength', 0), '文')
        else:
            return _MISSING
        return candidate if validator.is_valid(candidate) else _MISSING

    result = build(schema, arguments if arguments is not None else _MISSING)
    return None if result is _MISSING else result


def rejection_feedback(name, schema, arguments, value, message=None):
    required = required_parameters(schema)
    available = value.get('available_ids') if isinstance(value, dict) else None
    example = call_example(schema, arguments, available)
    details = value.get('details', ()) if isinstance(value, dict) else ()
    if not message:
        reason = value.get('reason', 'rejected') if isinstance(value, dict) else 'rejected'
        message = f'行动 {name} 未执行，原因：{reason}。'
    if details:
        message += '参数校验：' + '；'.join(
            f"{'.'.join(map(str, item.get('path', ()))) or 'arguments'}：{item['message']}" for item in details) + '。'
    message += '必填参数（名称:类型）：' + json.dumps(required, ensure_ascii=False) + '。'
    if example is not None:
        message += '正确参数示例：' + json.dumps(example, ensure_ascii=False) + '。'
    else:
        message += '请先按 action_describe 的 schema 补齐参数；对象 ID 须从当前查询结果取得。'
    if isinstance(value, dict) and 'available_ids' in value:
        message += '可用 ID：' + json.dumps(value['available_ids'], ensure_ascii=False) + '。'
    return message


def result_message(name, result, *, read_only=False, arguments=None):
    if result.message:
        return result.message
    if isinstance(result.value, dict) and result.value.get('message'):
        return result.value['message']
    if result.status == 'accepted':
        return f'行动 {name} 已受理，尚未完成；请按返回的进程引用查询后续状态。'
    if read_only:
        return query_message(name, result.value, arguments)
    if isinstance(result.value, dict) and result.value.get('changed') is False:
        return f'行动 {name} 已完成，内容未变化；现有状态继续有效，无需重复提交。'
    return f'行动 {name} 已完成，结果已生效，无需再次调用确认。'


def query_message(name, value, arguments=None):
    if isinstance(value, dict):
        total = next((value[key] for key in ('total', 'total_count', 'count') if key in value), None)
        items = next((value[key] for key in ('items', 'results', 'records', 'rows') if isinstance(value.get(key), list)), None)
        if total is not None and items is not None:
            continuation = {key: value[key] for key in ('next_cursor', 'next_page', 'next_offset', 'continuation')
                            if value.get(key) is not None}
            if not continuation and value.get('has_more') and isinstance(arguments, dict):
                if 'offset' in arguments:
                    continuation = {'offset': arguments['offset'] + len(items)}
                elif 'page' in arguments:
                    continuation = {'page': arguments['page'] + 1}
            for key in ('page_size', 'limit'):
                if continuation and isinstance(arguments, dict) and key in arguments:
                    continuation[key] = arguments[key]
            more = ''
            if continuation:
                returned = [key for key in continuation if key in value]
                if returned:
                    more = '；继续读取时沿用查询条件，使用返回的 ' + '、'.join(returned)
                else:
                    more = '；继续读取时沿用查询条件，传入 ' + json.dumps(continuation, ensure_ascii=False)
            if value.get('has_more') and not continuation:
                more = '；还有后续结果，请按 action_describe 的分页参数继续读取'
            return f'查询 {name} 完成，共 {total} 项，本次返回 {len(items)} 项{more}。'
    return f'查询 {name} 已完成，结果见结构化字段；无需重复查询确认。'


def tool_feedback(name, value, arguments):
    """在统一回执边界补充说明，保留工具原始结构和领域消息位置。"""
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, dict):
        return value
    if name == 'action_invoke' and isinstance(value.get('result'), dict):
        return value
    if value.get('error'):
        location = arguments.get('path', arguments.get('name', '当前输入'))
        return {**value, 'message': f'工具 {name} 在 {location} 未完成（{value["error"]}）：' +
                str(value.get('message', '请核对路径、权限和参数后重试。'))}
    if value.get('message'):
        return value
    if name == 'read' and 'total_bytes' in value:
        start = arguments.get('offset') or 0
        end = value['next_offset'] if value.get('next_offset') is not None else value['total_bytes']
        more = f'继续读取时传 offset={end}，并沿用返回的 revision 作为 expected_revision。' if value.get('next_offset') is not None else '已读取至文件末尾。'
        message = f'已读取 {value.get("path", arguments.get("path"))} 的 {end-start} 字节（总计 {value["total_bytes"]} 字节）。{more}'
    elif name == 'action_describe':
        if isinstance(value.get('actions'), list):
            descriptions = [tool_feedback(name, item, {'name': action}) for action, item in zip(arguments['actions'], value['actions'])]
            value = {**value, 'actions': descriptions}
            succeeded = sum(not item.get('error') for item in descriptions)
            message = f'已取得 {succeeded} 个行动的完整说明和参数 schema，共请求 {len(descriptions)} 个；按返回的参数名调用 action_invoke。'
        else:
            message = f'已取得行动 {value.get("name", arguments.get("name"))} 的完整说明和参数 schema；按返回的参数名调用 action_invoke。'
    else:
        message = query_message(name, value, arguments)
        if value.get('next_cursor') is not None:
            message += '继续读取时原样复制 next_cursor，并沿用本次查询条件。'
        if name == 'grep' and value.get('result_path'):
            message += f'全部匹配已保存至 {value["result_path"]}，使用 read 按 next_offset 读取该结果文件。'
    return {**value, 'message': message}
