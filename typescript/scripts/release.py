"""从指定干净提交串行构建两侧，直接比较后生成发行候选。"""
from pathlib import Path
import argparse,json,os,shutil,subprocess,sys,tarfile,time
from acceptance_pack import pack
from release_lock import prepare_dependencies
from release_compare import compare

def request(root, commit, output):
    root = root.resolve()
    repo = root.parent
    W = repo.parent
    output = Path(output).resolve()
    parent = W / 'evidence/release'
    assert output.is_relative_to(parent) and output != parent, 'RELEASE_OUTPUT_LOCATION ' + str(output)
    assert not output.exists(), 'RELEASE_OUTPUT_EXISTS ' + str(output)
    worktrees = {side: W / 'work' / ('release-' + output.name + '-' + side) for side in ['a', 'b']}
    for path in worktrees.values():
        assert not path.exists() and not path.is_symlink(), 'RELEASE_WORKTREE_EXISTS ' + str(path)
    result = subprocess.run(['git', 'rev-parse', '--verify', commit + '^{commit}'], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, 'RELEASE_COMMIT ' + commit + ': ' + result.stderr.strip()
    return {'root': root, 'repo': repo, 'W': W, 'output': output, 'commit': result.stdout.strip(), 'worktrees': worktrees, 'created': []}

def execute(command, cwd, log):
    started = time.monotonic()
    with log.open('w') as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    log.with_suffix(log.suffix + '.json').write_text(json.dumps({'command': [str(x) for x in command], 'cwd': str(cwd), 'exitCode': result.returncode, 'seconds': time.monotonic() - started}, indent=2) + '\n')
    assert result.returncode == 0, 'RELEASE_COMMAND_FAILED ' + str(log)

def native_references(build, destination):
    for folder in ['sqlite-native', 'random-native']:
        source = build / folder
        record = json.loads((source / 'NATIVE-BUILD.json').read_text())
        for relative in [*record['outputs'], 'NATIVE-BUILD.json']:
            target = destination / folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
            assert target.read_bytes() == (source / relative).read_bytes(), 'RELEASE_NATIVE_REFERENCE ' + str(target)

def archive_records(directory, commit):
    files = sorted(directory.glob('*.tgz'))
    assert len(files) == 13, 'RELEASE_PACKAGE_COUNT ' + str(directory)
    records = []
    for file in files:
        with tarfile.open(file, 'r:gz') as archive:
            source = json.load(archive.extractfile('package/SOURCE.json'))
            implementation = source['implementation']
            assert implementation['gitCommit'] == commit and implementation['dirty'] is False and implementation['release'] is True, 'RELEASE_PACKAGE_IDENTITY ' + file.name
            record = {'filename': file.name, 'bytes': file.stat().st_size, 'implementation': implementation}
            if 'package/NATIVE-BUILD.json' in archive.getnames():
                record['nativeBuild'] = json.load(archive.extractfile('package/NATIVE-BUILD.json'))
            records.append(record)
    return records

def catalog_gate(root, plan, side):
    try:
        execute(['node', 'scripts/catalog-config.mjs', '--check'], root, side / 'catalog-config.log')
        execute(['python3', '-B', 'scripts/catalog-reconcile.py', str(plan)], root, side / 'catalog-reconcile.log')
    except AssertionError as error:
        raise RuntimeError('RELEASE_CATALOG_STALE ' + str(side) + '; inspect catalog logs and regenerate the catalog') from error

def regression_receipt(side):
    rows = [json.loads(line) for line in (side / 'regression.log').read_text().splitlines()]
    resources = json.loads((side / 'regression/resources.json').read_text())
    summary = rows[-1]['summary']
    assert summary == {'files': len(resources), 'failed': []} and resources, 'RELEASE_REGRESSION_INCOMPLETE ' + str(side)
    assert [row['file'] for row in rows[:-1]] == [row['file'] for row in resources], 'RELEASE_REGRESSION_ORDER ' + str(side)
    assert all(row['exitCode'] == 0 for row in resources), 'RELEASE_REGRESSION_FAILED ' + str(side)
    return summary

def pack_cohort(root, folders, side):
    destination = side / 'tarballs'
    destination.mkdir()
    for folder in folders:
        try:
            pack(root, root / '.build' / folder, destination)
        except subprocess.CalledProcessError as error:
            (side / 'pack-error.stdout.log').write_text(error.stdout or '')
            (side / 'pack-error.stderr.log').write_text(error.stderr or '')
            raise RuntimeError('RELEASE_PACK_FAILED ' + folder + '; inspect ' + str(side / 'pack-error.stderr.log')) from error

def build_side(context, label):
    output = context['output']
    side = output / label
    side.mkdir()
    worktree = context['worktrees'][label]
    execute(['git', 'worktree', 'add', '--detach', str(worktree), context['commit']], context['repo'], side / 'worktree-add.log')
    context['created'].append(worktree)
    root = worktree / 'typescript'
    dependencies = prepare_dependencies(root, context['root'] / 'node_modules')
    (side / 'dependencies.json').write_text(json.dumps(dependencies, indent=2) + '\n')
    execute(['python3', '-B', 'scripts/build.py', '--release'], root, side / 'build.log')
    release = json.loads((root / '.build/release.json').read_text())
    assert release['release'] is True and release['implementation'] == {'gitCommit': context['commit'], 'dirty': False}, 'RELEASE_BUILD_IDENTITY ' + label
    assert len(release['packages']) == len(set(release['packages'])) == 13, 'RELEASE_BUILD_PACKAGES ' + label
    if label == 'a':
        catalog_gate(root, context['W'] / 'inputs/society0_next_refactor_v3/catalog/plan.json', side)
        execute(['python3', '-B', str(context['root'] / 'scripts/release_regression.py'), str(root), str(side / 'regression'), context['commit']], root, side / 'regression.log')
        regression_receipt(side)
        shutil.copytree(root / '.build/acceptance', output / 'acceptance-inputs')
        shutil.copy2(root / '.build/release.json', output / 'release.json')
    pack_cohort(root, release['packages'], side)
    archive_records(side / 'tarballs', context['commit'])
    reference = 'native-reference' if label == 'a' else 'native-reference-b'
    native_references(root / '.build', output / reference)
    shutil.copy2(root / '.build/release.json', side / 'release.json')
    assert worktree in context['created'] and worktree.resolve() == worktree and worktree.parent == context['W'] / 'work', 'RELEASE_WORKTREE_OWNERSHIP ' + str(worktree)
    execute(['git', 'worktree', 'remove', '--force', str(worktree)], context['repo'], side / 'worktree-remove.log')
    assert not worktree.exists(), 'RELEASE_WORKTREE_REMAINS ' + str(worktree)

def discard_tarballs(output, label):
    target = output / label / 'tarballs'
    assert label in ['a', 'b'] and target.resolve() == target and target.is_dir(), 'RELEASE_TARBALL_OWNERSHIP ' + str(target)
    shutil.rmtree(target)

def publish_manifest(context):
    output = context['output']
    forbidden = [str(context['W']), str(context['root']), *[str(p) for p in context['worktrees'].values()], os.environ.get('HOME', ''), os.environ.get('TMPDIR', '')]
    report = compare(output / 'a/tarballs', output / 'b/tarballs', [p for p in forbidden if p])
    (output / 'reproducibility.json').write_text(json.dumps(report, indent=2) + '\n')
    if report['status'] != 'identical':
        for label in ['a', 'b']: discard_tarballs(output, label)
        raise RuntimeError('RELEASE_NOT_REPRODUCIBLE ' + str(output / 'reproducibility.json'))
    source = output / 'a/tarballs'
    target = output / 'tarballs'
    assert source.resolve() == source and source.is_dir() and not target.exists(), 'RELEASE_PROMOTION_OWNERSHIP ' + str(source)
    source.rename(target)
    discard_tarballs(output, 'b')
    release = json.loads((output / 'release.json').read_text())
    manifest = {'format': 'society-release/v1', 'release': True, 'implementation': release['implementation'], 'tarballs': archive_records(target, context['commit']), 'reproducibility': 'reproducibility.json', 'nativeReference': 'native-reference'}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('commit', help='要冻结的Git提交或引用')
    parser.add_argument('output', type=Path, help='W/evidence/release下尚不存在的候选目录')
    args = parser.parse_args()
    context = request(Path(__file__).resolve().parents[1], args.commit, args.output)
    context['output'].mkdir(parents=True)
    for side in ['a', 'b']: build_side(context, side)
    print(json.dumps(publish_manifest(context)))

if __name__ == '__main__': main()
