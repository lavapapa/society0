"""正式 starter 两轮及可信完整点恢复；凭据只由 TTY 输入。"""
import argparse
import asyncio
import getpass
import importlib.util
import json
from pathlib import Path
import time

from society0.kernel.runner import run_plan
from society0.kernel.storage import StageReader
from society0.kernel.results import Results
from society0.kernel.threads import _messages
from society0.kernel.usage import COUNTERS
from society0.kernel.memory import _payload

ROOT = Path(__file__).resolve().parents[3]


def inspect_run(path, message, *, expected_dimension=1024, physical=True):
    with StageReader(path) as reader:
        results = Results(reader)
        complete = reader.read(lambda v: v.complete_step)
        views = reader.read(lambda v: v.query('SELECT actor FROM news_views'))
        memories = reader.read(lambda v: v.query('SELECT actor,timestamp,state FROM memory_rows'))
        memory_payloads = reader.read(lambda v: [_payload(v, row[0]) for row in v.query('SELECT id FROM memory_rows')])
        vectors = reader.read(lambda v: v.query('SELECT dimension FROM memory_vectors'))
        jobs = reader.read(lambda v: v.query('SELECT actor,timestamp,state FROM memory_jobs'))
        heads = reader.read(lambda v: v.query('SELECT id,actor,kind,status,moment FROM thread_heads ORDER BY ordinal'))
        messages = {row[0]: reader.read(lambda v, tid=row[0]: _messages(v, tid)) for row in heads}
        responses = {}
        for step in (1, 2):
            reference = results.phase(step, 0)['tables']['responses']
            responses[str(step)] = reader.read(lambda v: Results._value(v, reference['id'], 0))
        usage = []
        if physical:
            for prefix, kind in (('thread', 'llm'), ('resource', 'embedding')):
                for row in reader.read(lambda v, prefix=prefix: v.query(f'SELECT id,model,{",".join(COUNTERS)} FROM {prefix}_usage_calls')):
                    usage.append({'id': row[0], 'kind': kind, 'model': row[1], **dict(zip(COUNTERS, row[2:]))})
        calls = [(row['kind'], row['id'], row['model']) for row in usage]
    assert complete == 2
    assert views == [('alice',)], views
    assert all(row['status'] == 'completed' for row in responses.values())
    survey = responses['2']['value']['result']
    assert type(survey['credibility']) is int and 1 <= survey['credibility'] <= 7
    assert isinstance(survey['reason'], str) and survey['reason'].strip()
    assert memories and all(actor == 'alice' and timestamp == 1 and state == 'ready' for actor, timestamp, state in memories)
    assert vectors and all(dimension == expected_dimension for dimension, in vectors)
    assert jobs and all(actor == 'alice' and timestamp == 1 for actor, timestamp, state in jobs)
    assert message in json.dumps(messages, ensure_ascii=False)
    assert responses['1']['value']['action_counts']['news.view_details'] == 1
    interview_messages = messages[responses['2']['value']['thread_id']]
    assert message in json.dumps(interview_messages, ensure_ascii=False)
    recalled = [json.loads(item['content'])['recalled_memories'] for item in interview_messages
                if item.get('role') == 'user' and isinstance(item.get('content'), str)
                and item['content'].startswith('{"recalled_memories":')]
    assert recalled and recalled[0]
    assert all(text in [payload['content'] for payload in memory_payloads] for text in recalled[0])
    # 完整 Thread 和结果原样留证；恢复结果的随机模型回答无需逐字相等。
    return {'complete_step': complete, 'news_views': views, 'memories': memories,
            'vectors': vectors, 'jobs': jobs, 'memory_payloads': memory_payloads, 'heads': heads, 'messages': messages,
            'responses': responses, 'calls': calls, 'usage': usage}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    key = getpass.getpass('SiliconFlow API key (hidden): ')
    endpoint = {'id': 'siliconflow', 'base_url': 'https://api.siliconflow.cn/v1',
                'api_key': key, 'concurrency': 1, 'timeout': 60, 'trust_env': False}
    model = {'endpoints': [{**endpoint, 'model': 'Qwen/Qwen3.8-27B'}],
             'request_options': {'max_tokens': 1024, 'temperature': 0,
                                 'parallel_tool_calls': False, 'tool_choice': 'auto',
                                 'extra_body': {'enable_thinking': False},
                                 'openai_continuous_usage_stats': True}}
    embedding = {'endpoints': [{**endpoint, 'model': 'Qwen/Qwen3-Embedding-0.6B', 'send_dimensions': False}],
                 'dimensions': 1024}
    spec = importlib.util.spec_from_file_location('official_starter', ROOT / 'skill/assets/minimal_experiment.py')
    starter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(starter)
    def plan():
        return starter.build_plan(release={'commit': args.release}, model=model, embedding=embedding)
    started = time.monotonic()
    await run_plan(args.output / 'continuous', plan())
    first = inspect_run(args.output / 'continuous', starter.MESSAGE)
    (args.output / 'starter-continuous.json').write_text(json.dumps(first, ensure_ascii=False, indent=2))
    await run_plan(args.output / 'restored', plan(), source=args.output / 'continuous', step=1)
    restored = inspect_run(args.output / 'restored', starter.MESSAGE)
    (args.output / 'starter-restored.json').write_text(json.dumps(restored, ensure_ascii=False, indent=2))
    common = {(row[0], row[1]) for row in first['calls']}
    unique = common | {(row[0], row[1]) for row in restored['calls']}
    facts = {}
    for item in first['usage'] + restored['usage']:
        identity = (item['kind'], item['id'])
        if identity in facts and facts[identity] != item:
            raise ValueError('same physical identity has inconsistent facts')
        facts[identity] = item
    report = {'passed': True, 'release': args.release, 'elapsed_s': time.monotonic() - started,
              'continuous_physical_calls': len(first['calls']),
              'restore_new_physical_calls': sum((row[0], row[1]) not in common for row in restored['calls']),
              'unique_physical_calls': len(unique), 'monitor_warning_above_30_calls': len(unique) > 30,
              'physical_usage': list(facts.values()),
              'model_profile': {**model, 'endpoints': [{k: v for k, v in model['endpoints'][0].items() if k != 'api_key'}]},
              'embedding_profile': {**embedding, 'endpoints': [{k: v for k, v in embedding['endpoints'][0].items() if k != 'api_key'}]}}
    (args.output / 'starter-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
