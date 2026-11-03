"""安装验收逐命令完整日志，以及与后续台账门分离的写一次测试收据。"""
import json,resource,subprocess,time

def run_command(command, cwd, output, records):
    directory = output / 'commands'
    directory.mkdir(exist_ok=True)
    name = str(len(records) + 1).zfill(3)
    stdout = directory / (name + '.stdout.log')
    stderr = directory / (name + '.stderr.log')
    command = list(map(str, command))
    started = time.monotonic()
    with stdout.open('w') as out, stderr.open('w') as err:
        result = subprocess.run(command, cwd=cwd, stdout=out, stderr=err)
    record = {'command': command, 'cwd': str(cwd), 'exitCode': result.returncode, 'seconds': time.monotonic() - started, 'stdout': str(stdout.relative_to(output)), 'stderr': str(stderr.relative_to(output)), 'cumulativeChildPeakRssKiB': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}
    records.append(record)
    (output / 'commands.json').write_text(json.dumps(records, indent=2) + '\n')
    assert record['cumulativeChildPeakRssKiB'] <= 524288, 'ACCEPTANCE_RSS_LIMIT ' + str(output / 'commands.json')
    assert result.returncode == 0, 'ACCEPTANCE_COMMAND_FAILED ' + str(stderr)
    return record

def write_test_receipt(output, value, records):
    assert records, 'ACCEPTANCE_TESTS_INCOMPLETE ' + str(output)
    assert all(record['exitCode'] == 0 for record in records), 'ACCEPTANCE_TESTS_INCOMPLETE ' + str(output)
    with (output / 'result.json').open('x') as stream:
        json.dump({**value, 'status': 'passed', 'commands': records}, stream, indent=2)
        stream.write('\n')
