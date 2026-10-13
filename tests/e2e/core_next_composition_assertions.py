"""只读公共组合工件验收；结构化报告给出首项差异及事实位置。"""
import argparse
import json
from pathlib import Path
import tempfile

from society0.kernel.interaction import Moment
from society0.kernel.storage import StageReader, StageStore
from society0.kernel.threads import ThreadStore
from examples.core_next.composed_cognition import SOCIAL_ID, NOTICE_ID, LIFECYCLE_IDS, original


def read_run(path):
    path = Path(path)
    manifest = json.loads((path/'runner.json').read_text(encoding='utf-8'))
    config = manifest['contract']['configuration']
    with StageReader(path) as reader:
        threads = ThreadStore(reader)
        moment = config['moment']
        tid = threads.find('a', Moment(moment['time'], moment['phase']),
                           'interview' if config['variant'] == 'interview' else 'decision')
        def data(view):
            return {'complete': view.complete_step, 'run_id': view.run_id,
                'requests': [(step, actor, thread, sequence, through, json.loads(typed), json.loads(options))
                    for step, actor, thread, sequence, through, typed, options in view.query('SELECT step,actor,thread_id,request_seq,through,typed,options FROM composition_requests ORDER BY id')],
                'diagnostics': [(step, actor, kind, json.loads(detail)) for step, actor, kind, detail in view.query('SELECT step,actor,kind,detail FROM composition_diagnostics ORDER BY id')],
                'outcomes': [(step, actor, json.loads(result)) for step, actor, result in view.query('SELECT step,actor,result FROM composition_outcomes ORDER BY id')],
                'posts': view.query('SELECT id,view_count FROM social_posts ORDER BY id'),
                'receipts': {domain: view.query('SELECT actor,thread_id,notice_id FROM "'+domain+'_receipts" ORDER BY actor,thread_id,notice_id')
                    for domain in (('notice.data',) if config['case'] == 'multi-source' else ('notice.a.data', 'notice.b.data'))}}
        result = reader.read(data)
        result.update(path=str(path), manifest=manifest, config=config, thread_id=tid,
            messages=[] if tid is None else threads.read_messages(tid),
            raw_messages=[] if tid is None else threads.snapshot_messages(tid, raw=True)['messages'],
            cursor=None if tid is None else threads.input_cursor(tid, 'operating_context'))
        for row in result['requests']:
            request = threads.read_request(row[2], row[3])
            if request['through'] != row[4]:
                raise AssertionError('request watermark differs from stored event')
    return result


def checkpoint(path, step, config):
    with tempfile.TemporaryDirectory(prefix='society0-composition-read-') as temporary:
        target = Path(temporary)/'view'
        StageStore.prepare_readonly(path, target, step=step)
        # 派生只读视图使用原 runner 配置定位同一 Thread。
        with StageReader(target) as reader:
            threads = ThreadStore(reader)
            tid = threads.find('a', Moment(config['moment']['time'], config['moment']['phase']),
                               'interview' if config['variant'] == 'interview' else 'decision')
            return None if tid is None else threads.input_cursor(tid, 'operating_context')


def texts(typed):
    return [part['content'] for message in typed for part in message['parts']
            if part['part_kind'] in ('system-prompt', 'user-prompt', 'text')]


def contains_original(body, text):
    if text.startswith('推荐动态\n'):
        payload = json.loads(text.split('\n', 1)[1])
        return any(post['content'] == body for post in payload['new_posts'])
    return body in text


def stable_messages(typed):
    result = []
    for message in typed:
        # SDK 构造时刻不属于业务内容；只移除消息及 part 的 timestamp 字段。
        item = {key: value for key, value in message.items() if key != 'timestamp'}
        item['parts'] = [{key: value for key, value in part.items() if key != 'timestamp'} for part in item['parts']]
        if item.get('provider_response_id') is not None:
            import uuid
            identifier = uuid.UUID(item['provider_response_id'])
            if identifier.version != 4 or identifier.hex != item['provider_response_id']:
                raise ValueError('offline fixture response ID must be a generated UUID4')
            item['provider_response_id'] = 'response-'+str(sum(message['kind'] == 'response' for message in result)+1)
        result.append(item)
    return result


def validate(path, *, case, compare=None, failed=None):
    if case in ('registration','manifest','shutdown','extensions'):
        if case in ('registration','manifest'):
            from examples.core_next.composed_registration_manifest import validate as domain_validate, compare as domain_compare
            evidence=[json.loads(line) for line in (Path(path)/'composition-evidence.jsonl').read_text(encoding='utf-8').splitlines()]
            config=evidence[0]['config']
        else:
            from examples.core_next.composed_shutdown_extensions import validate as domain_validate, compare as domain_compare
            config=json.loads((Path(path)/'runner.json').read_text(encoding='utf-8'))['contract']['configuration']
        checks=domain_validate(path)
        if compare is not None: checks.extend(domain_compare(compare,path))
        with StageReader(path) as reader: run_id=reader.read(lambda view:view.run_id)
        checks.insert(0,{'id':'case','passed':case==config['case'],'expected':case,'actual':config['case'],'location':'configuration/case'})
        return {'format':1,'case':case,'variant':config['variant'],'source_commit':config['release']['commit'],
            'run_id':run_id,'checks':checks,'first_difference':next((check for check in checks if not check['passed']),None)}
    manifest=json.loads((Path(path)/'runner.json').read_text(encoding='utf-8'))
    config=manifest['contract']['configuration']
    if case=='multi-source' and config['variant'] in ('empty-values','concurrent','moment-switch'):
        from examples.core_next.composed_source_edges import validate as edge_validate,compare as edge_compare
        checks=edge_validate(path)
        if compare is not None:checks.extend(edge_compare(compare,path))
        with StageReader(path) as reader: run_id=reader.read(lambda view:view.run_id)
        return {'format':1,'case':case,'variant':config['variant'],'source_commit':manifest['contract']['release']['commit'],
            'run_id':run_id,'checks':checks,'first_difference':next((check for check in checks if not check['passed']),None)}
    if case in ('actor-directory', 'artifact-read', 'recommendation', 'configuration'):
        manifest = json.loads((Path(path)/'runner.json').read_text(encoding='utf-8'))
        config = manifest['contract']['configuration']
        if case == 'configuration':
            from examples.core_next.composed_configuration import validate as domain_validate, compare as domain_compare
            checks = domain_validate(path)
            if compare is not None: checks.extend(domain_compare(compare, path))
        elif case == 'recommendation':
            from examples.core_next.composed_recommendation import validate as domain_validate, compare as domain_compare
            checks = domain_validate(path)
            if compare is not None: checks.extend(domain_compare(compare, path))
        else:
            from examples.core_next.composed_directory_artifacts import validate as domain_validate, compare as domain_compare
            checks = domain_validate(path)
            if compare is not None: checks.extend(domain_compare(compare, path))
        checks.insert(0, {'id': 'case', 'passed': case == config['case'], 'expected': case, 'actual': config['case'],
                          'location': 'runner.json/contract/configuration/case'})
        with StageReader(path) as reader:
            run_id = reader.read(lambda view: view.run_id)
        return {'format': 1, 'case': case, 'variant': config['variant'], 'source_commit': manifest['contract']['release']['commit'],
                'run_id': run_id, 'checks': checks, 'first_difference': next((check for check in checks if not check['passed']), None)}
    run = read_run(path)
    config = run['config']
    checks = []
    def check(identifier, expected, actual, location):
        checks.append({'id': identifier, 'passed': actual == expected, 'expected': expected, 'actual': actual, 'location': location})
    check('case', case, config['case'], 'runner.json/contract/configuration/case')
    check('complete-step', config['end'], run['complete'], 'metadata.json/complete_step')
    check('run-identity', run['manifest']['run_id'], run['run_id'], 'metadata.json/run_id')
    selected = set(config['sources'])
    positions = {} if run['cursor'] is None else run['cursor']['position']
    expected_positions = {}
    source = run['manifest']['source']
    if source is not None:
        before = checkpoint(source['path'], source['step'], config)
        if before is not None: expected_positions.update(before['position'])
    discarded = config['variant'] == 'discarded'
    if not discarded:
        for identifier in selected:
            if identifier == SOCIAL_ID:
                expected_positions[identifier] = list(f'post_{number}' for number in range(1, config['end']+2))
            else:
                expected_positions[identifier] = config['end']
    comparable_positions = {identifier: value['seen'] if identifier == SOCIAL_ID else value for identifier, value in positions.items()}
    expected_positions = {identifier: value['seen'] if identifier == SOCIAL_ID and isinstance(value, dict) else value for identifier, value in expected_positions.items()}
    check('source-positions', expected_positions, comparable_positions, f'thread_input_cursors/{run["thread_id"]}/operating_context')
    received = {}
    for domain, rows in run['receipts'].items():
        identifier = NOTICE_ID if domain == 'notice.data' else 'notice.'+domain.split('.')[1]+'.v1'
        watermark = comparable_positions.get(identifier, 0)
        received[domain] = rows
        check('receipts', [('a', run['thread_id'], number) for number in range(1, watermark+1)], rows,
              domain+'_receipts/actor=a')
    expected_views = [(identifier, 1 if SOCIAL_ID in selected and not discarded else 0) for identifier, views in run['posts']]
    check('exposure', expected_views, run['posts'], 'social_posts/id,view_count')
    requests = run['requests']
    check('request-count', config['end']*2, len(requests), 'composition_requests/id')
    check('activation-outcomes', ['completed']*(config['end']*2), [row[2]['status'] for row in run['outcomes']], 'composition_outcomes/result/status')
    if config['variant'] == 'interview':
        check('structured-measurement', [{'measurement': '完整测量🙂'}]*(config['end']*2),
              [row[2]['value']['result'] for row in run['outcomes']], 'composition_outcomes/result/value/result')
    if config.get('transport') == 'sdk-offline':
        responses = [message['model_message']['provider_response_id'] for message in run['raw_messages']
                     if message.get('model_message', {}).get('kind') == 'response']
        references = [message['provider_response_id'] for row in requests for message in row[5] if message['kind'] == 'response']
        check('generated-response-identities', config['end']*2, len(set(responses)), 'thread_messages/model_message/provider_response_id')
        check('generated-response-references', True, all(identifier in responses for identifier in references),
              'composition_requests/typed/provider_response_id -> thread_messages/model_message/provider_response_id')
    originals = []
    if config['case'] == 'multi-source':
        if SOCIAL_ID in comparable_positions:
            originals.extend(original('social', 1, activation) for activation in (1, 2))
            originals.extend(original('social', number, 2) for number in range(2, config['end']+1))
        if NOTICE_ID in comparable_positions:
            originals.extend(original('notice', number) for number in range(1, comparable_positions[NOTICE_ID]+1))
    else:
        for identifier, watermark in comparable_positions.items():
            domain = 'notice.'+identifier.split('.')[1]+'.data'
            originals.extend(original(domain, number) for number in range(1, watermark+1))
    if requests:
        final_texts = texts(requests[-1][5])
        occurrences = [sum(contains_original(body, text) for text in final_texts) for body in originals]
        check('request-history', [1]*len(originals), occurrences, f'composition_requests/request_seq={requests[-1][3]}/typed/parts/content')
        for activation_number, row in enumerate(requests, 1):
            if not discarded:
                check('single-background', 1, sum('完整主体背景🙂' in text for text in texts(row[5])),
                    f'composition_requests/request_seq={row[3]}/typed/parts/content')
                check('one-new-state-per-activation', activation_number, sum(text.count('当前主观状态') for text in texts(row[5])),
                    f'composition_requests/request_seq={row[3]}/typed/parts/content')
        check('request-options', True, all(row[6]['parallel_tool_calls'] is False and bool(row[6]['tools']) and all(tool['function']['parameters'] for tool in row[6]['tools']) for row in requests),
              'composition_requests/options/tools/function/parameters,parallel_tool_calls')
    check('successful-diagnostics', [], [row for row in run['diagnostics'] if row[2] != 'sentinel'], 'composition_diagnostics/kind')
    if compare is not None:
        other = read_run(compare)
        check('equivalent-source-positions', other['cursor'], run['cursor'], 'thread_input_cursors/operating_context/cursor')
        check('equivalent-exposure', other['posts'], run['posts'], 'social_posts/id,view_count')
        expected_requests = [(row[0], row[1], row[3], row[4], stable_messages(row[5]), row[6]) for row in other['requests']]
        actual_requests = [(row[0], row[1], row[3], row[4], stable_messages(row[5]), row[6]) for row in requests]
        check('equivalent-requests', expected_requests, actual_requests, 'composition_requests/typed,options,request_seq,through')
    if failed is not None:
        interrupted = read_run(failed)
        fault = interrupted['config']['fault']
        number = interrupted['config']['fault_step']
        status = json.loads((Path(failed)/'runner-status.json').read_text(encoding='utf-8'))
        check('failure-status', 'failed', status['status'], 'failed/runner-status.json/status')
        check('failure-exception', 'TypeError' if fault.startswith('append-invalid-') else 'CompositionFault', status['error'], 'failed/runner-status.json/error')
        check('failure-boundary', number-1, interrupted['complete'], 'failed/metadata.json/complete_step')
        check('fault-kind', [fault], [row[2] for row in interrupted['diagnostics'] if row[2] != 'sentinel'], 'failed/composition_diagnostics/kind')
        if fault == 'after-callback-partial-write':
            check('no-sentinel-after-fault', [], [row for row in interrupted['diagnostics'] if row[0] == number and row[2] == 'sentinel'], 'failed/composition_diagnostics/step,kind')
            check('no-provider-after-fault', [], [row for row in interrupted['requests'] if row[0] == number], 'failed/composition_requests/step')
            check('partial-receipt-present', True, any(row[2] == number for row in interrupted['receipts']['notice.data']), 'failed/notice.data_receipts/notice_id')
        check('restore-source', {'path': str(Path(failed).absolute()), 'step': number-1}, source, 'runner.json/source')
    first = next((item for item in checks if not item['passed']), None)
    return {'format': 1, 'case': case, 'variant': config['variant'], 'source_commit': run['manifest']['contract']['release']['commit'],
            'run_id': run['run_id'], 'checks': checks, 'first_difference': first}


def main():
    parser = argparse.ArgumentParser(description='只读核对公共组合运行的请求、来源位置与恢复事实')
    parser.add_argument('--case', required=True)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--log', type=Path)
    parser.add_argument('--exit-code', type=int)
    parser.add_argument('--continuous', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--failed-run', type=Path)
    args = parser.parse_args()
    try:
        if args.config is not None or args.log is not None or args.exit_code is not None:
            if args.case!='configuration' or args.config is None or args.log is None or args.exit_code is None:
                raise ValueError('startup validation requires configuration case, config, log and exit-code')
            from examples.core_next.composed_configuration import validate_negative
            result = validate_negative(args.config,args.log,args.exit_code,args.run)
        else:
            if args.run is None: raise ValueError('run artifact directory is required')
            result = validate(args.run, case=args.case, compare=args.continuous, failed=args.failed_run)
        status = 0 if result['first_difference'] is None else 1
    except (FileNotFoundError, KeyError, ValueError) as error:
        result = {'format': 1, 'case': args.case, 'variant': None, 'source_commit': None, 'run_id': None,
            'checks': [], 'first_difference': {'id': 'input', 'passed': False, 'expected': 'complete public artifacts',
                'actual': type(error).__name__, 'location': str(args.run)}}
        status = 2
    payload = json.dumps(result, ensure_ascii=False)
    if args.output is not None: args.output.write_text(payload+'\n', encoding='utf-8')
    print(payload)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
