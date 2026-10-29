from pathlib import Path
import json,sys
root=Path(__file__).resolve().parents[1]
coverage=Path(sys.argv[1]).resolve();consumer=coverage.parent/'consumer';original={}
for file in (consumer/'node_modules/@society0/host/dist').rglob('*.js.map'):
    data=json.loads(file.read_text())
    for name,content in zip(data['sources'],data['sourcesContent']):
        original[(file.parent/data.get('sourceRoot','')/name).resolve()]=content
mapped=[];count=0
for line in (coverage/'lcov.info').read_text().splitlines():
    if line.startswith('SF:'):
        relative=Path(line[3:]);assert relative.parts[:3]==('node_modules','@society0','host'),line
        target=root/'packages'/Path(*relative.parts[2:])
        assert original[(consumer/relative).resolve()].encode()==target.read_bytes(),f'coverage source changed: {target}'
        line='SF:'+str(target);count+=1
    mapped.append(line)
assert count
output=Path(sys.argv[2]).resolve()
if output.exists(): output.read_text()
output.write_text('\n'.join(mapped)+'\n')
print('Verified inline TS sources:',count)
