from pathlib import Path
import json,subprocess,shutil
root=Path(__file__).resolve().parents[1];w=root.parent.parent
out=w/'evidence/pr02/host-quality-consumer-01';out.mkdir();tar=out/'tarballs';tar.mkdir();consumer=out/'consumer';consumer.mkdir()
(consumer/'package.json').write_text(json.dumps({'name':'host-quality-consumer','private':True,'type':'module'}))
old=w/'evidence/pr02/consumer-05/tarballs'
prefixes=['society0-cosmokit-','society0-schemastery-','society0-cordis-','society0-loader-','society0-include-','society0-group-','society0-cli-','standard-schema-spec-','js-yaml-','argparse-','p-limit-','yocto-queue-','resolve.exports-','semver-','fixtures-provider-','fixtures-consumer-','fixtures-jsdoc-']
archives=[file for file in old.glob('*.tgz') if any(file.name.startswith(prefix) for prefix in prefixes)]
assert len(archives)==len(prefixes)
record=json.loads(subprocess.check_output(['npm','pack','--ignore-scripts','--json','--pack-destination',str(tar)],cwd=root/'.build/host',text=True))[0]
archives.append(tar/record['filename'])
subprocess.run(['npm','install','--offline','--ignore-scripts',*map(str,archives)],cwd=consumer,check=True)
for name in ['consumer.test.mjs','exports.test.mjs','peer-contract.test.mjs','pr02.test.mjs','cli.test.mjs']:
    text=(root/'tests'/name).read_text().replace('../.build/host/dist/index.js','@society0/host')
    (consumer/name).write_text(text)
shutil.copytree(root/'tests/pr02',consumer/'pr02')
subprocess.run(['node',str(root/'node_modules/c8/bin/c8.js'),'--reporter=lcov','--reporter=json-summary','--exclude-node-modules=false','--include=node_modules/@society0/host/**','--report-dir='+str(out/'coverage'),'node','--test','--test-concurrency=1','consumer.test.mjs','exports.test.mjs','peer-contract.test.mjs','pr02.test.mjs','cli.test.mjs'],cwd=consumer,check=True)
