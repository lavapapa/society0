"""执行未修改的build层选择器一次，保留完整输出与原生累计RSS。"""
from pathlib import Path
from functools import partial
import datetime,json,resource,runpy,subprocess,sys

def observed_run(real_run, root, output, records, *args, **kwargs):
    result = real_run(*args, **kwargs)
    command = args[0] if args else kwargs['args']
    if command and command[0] == 'node' and '--test' in command:
        file = Path(command[-1])
        assert file.parent == root / 'tests', 'RELEASE_REGRESSION_TEST_ROOT ' + str(file)
        (output / (file.name + '.stdout.log')).write_text(result.stdout or '')
        (output / (file.name + '.stderr.log')).write_text(result.stderr or '')
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        record = {'file': file.name, 'exitCode': result.returncode, 'cumulativeChildPeakRssKiB': peak, 'capKiB': 524288}
        records.append(record)
        (output / 'resources.json').write_text(json.dumps(records, indent=2) + '\n')
        if peak > 524288: raise RuntimeError('BUILD_TEST_RSS_LIMIT ' + json.dumps(record))
    return result

def run_regression(root, output, expected_commit):
    root = root.resolve()
    output = output.resolve()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    assert head == expected_commit, 'RELEASE_REGRESSION_COMMIT ' + head
    assert subprocess.check_output(['git', 'status', '--porcelain'], cwd=root) == b'', 'RELEASE_REGRESSION_DIRTY'
    output.mkdir(parents=True)
    records = []
    (output / 'start.json').write_text(json.dumps({'head': head, 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'selector': 'unchanged scripts/test-build.py build', 'retryPolicy': 'one complete ordered run; preserve failures', 'resourceMethod': 'native RUSAGE_CHILDREN cumulative maximum in dedicated collector process'}, indent=2) + '\n')
    real_run = subprocess.run
    argv, path = sys.argv[:], sys.path[:]
    try:
        subprocess.run = partial(observed_run, real_run, root, output, records)
        sys.path.insert(0, str(root / 'scripts'))
        sys.argv = [str(root / 'scripts/test-build.py'), 'build']
        runpy.run_path(str(root / 'scripts/test-build.py'), run_name='__main__')
    finally:
        subprocess.run = real_run
        sys.argv[:] = argv
        sys.path[:] = path

if __name__ == '__main__':
    if len(sys.argv) != 4: raise SystemExit('usage: release_regression.py <typescript-root> <fresh-report-directory> <expected-commit>')
    run_regression(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
