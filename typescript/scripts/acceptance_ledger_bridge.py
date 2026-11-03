"""最终安装先核实其余台账已就绪，成功收据随后补齐实际安装执行。"""
from pathlib import Path
import json,os,shlex
from acceptance_ledger import read_json,read_fragments,merge,ledger_errors,plan_ids,INSTALLED_COPIES,REWRITER

def pending_owner(W, output):
    return W / 'evidence/acceptance-pending' / ('installed-' + output.name)

def merged_ledger(W):
    errors, ledger = merge(read_json(W / 'evidence/acceptance.json'), read_fragments(W))
    assert not errors, 'ACCEPTANCE_LEDGER_MERGE ' + json.dumps(errors)
    return ledger

def installed_templates(ledger, paths):
    slots = set()
    deferred_logs = set()
    for case in ledger['cases']:
        missing = [e for e in case.get('executions', []) if not e.get('log')]
        ready = [e for e in missing if e.get('layer') == 'installed-e2e' and e.get('path') in paths]
        slots.update((case['id'], e['path']) for e in ready)
        if missing and len(missing) == len(ready): deferred_logs.add(case['id'])
    return slots, deferred_logs

def deferred_error(error, slots, log_ids):
    if error[0] == 'LOG_MISSING': return error[1] in log_ids
    if error[0] == 'CANDIDATE_REQUIRED': return (error[1], error[2]) in slots
    return False

def preflight(W, candidate, output, paths):
    candidate.relative_to(W)
    output.relative_to(W)
    assert not os.path.lexists(pending_owner(W, output)), 'ACCEPTANCE_PENDING_EXISTS ' + str(pending_owner(W, output))
    ledger = merged_ledger(W)
    slots, log_ids = installed_templates(ledger, paths)
    errors = ledger_errors(ledger, W, plan_ids(W), False)
    blocked = [error for error in errors if not deferred_error(error, slots, log_ids)]
    assert not blocked, 'ACCEPTANCE_LEDGER_NOT_READY ' + json.dumps(blocked)
    return {'ledger': ledger, 'remainingErrors': errors}

def execution_entry(W, candidate, output, source, command):
    entry = {'path': source, 'layer': 'installed-e2e', 'candidate': candidate.relative_to(W).as_posix(), 'log': (output / command['stdout']).relative_to(W).as_posix(), 'command': shlex.join(command['command'])}
    if Path(source).name in INSTALLED_COPIES: entry['rewrittenBy'] = REWRITER
    return entry

def receipt_executions(W, candidate, output, command, targets):
    assert (output / 'result.json').is_file(), 'ACCEPTANCE_RECEIPT_REQUIRED ' + str(output)
    receipt = read_json(output / 'result.json')
    assert receipt['status'] == 'passed', 'ACCEPTANCE_RECEIPT_FAILED ' + str(output)
    assert receipt['mode'] == 'candidate', 'ACCEPTANCE_RECEIPT_CANDIDATE ' + str(output)
    assert receipt['candidate'] == str(candidate), 'ACCEPTANCE_RECEIPT_CANDIDATE ' + str(candidate)
    assert command in receipt['commands'], 'ACCEPTANCE_RECEIPT_COMMAND ' + str(output)
    assert command['exitCode'] == 0, 'ACCEPTANCE_RECEIPT_COMMAND ' + str(output)
    assert Path(command['command'][0]).name == 'node', 'ACCEPTANCE_RECEIPT_COMMAND ' + str(output)
    assert '--test' in command['command'], 'ACCEPTANCE_RECEIPT_COMMAND ' + str(output)
    assert Path(command['cwd']).resolve() == output / 'consumer', 'ACCEPTANCE_RECEIPT_CONSUMER ' + str(output)
    assert (output / command['stdout']).is_file(), 'ACCEPTANCE_RECEIPT_LOG ' + str(output)
    assert (output / command['stderr']).is_file(), 'ACCEPTANCE_RECEIPT_LOG ' + str(output)
    executions = {}
    for source, target in targets.items():
        assert target in command['command'], 'ACCEPTANCE_TEST_NOT_EXECUTED ' + target
        executions[source] = execution_entry(W, candidate, output, source, command)
    return executions

def fragment_payloads(output, ledger, executions):
    texts = {source: (output / 'inputs/tests' / Path(source).relative_to('typescript/tests')).read_text() for source in executions}
    payloads = {}
    for case in ledger['cases']:
        entries = [execution for source, execution in executions.items() if case['id'] in texts[source]]
        if entries: payloads[case['id']] = {'appendExecutions': entries}
    return payloads

def write_installed_fragments(W, candidate, output, ledger, command, targets):
    executions = receipt_executions(W, candidate, output, command, targets)
    payloads = fragment_payloads(output, ledger, executions)
    assert payloads, 'ACCEPTANCE_NO_LEDGER_TESTS ' + str(output)
    directory = pending_owner(W, output)
    directory.mkdir(parents=True)
    for cid, payload in payloads.items():
        payload['appendEvidence'] = [(output / 'result.json').relative_to(W).as_posix()]
        with (directory / ('patch-' + cid + '.json')).open('x') as stream:
            json.dump(payload, stream, indent=2)
            stream.write('\n')
    return sorted(payloads)
