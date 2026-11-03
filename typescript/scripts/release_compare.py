"""发行与产物验收共用的绝对路径门禁。"""
import json,re,tarfile
HOST_ROOT=re.compile(r'^/(mnt|home|tmp|private|Users|root|opt|var|srv|data|nfs|scratch)(/|$)')

def json_strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for item in value.values():yield from json_strings(item)
    elif isinstance(value,list):
        for item in value:yield from json_strings(item)

def metadata_member(name):
    return name in ['package/SOURCE.json','package/NATIVE-BUILD.json','package/package.json'] or (name.startswith('package/generated/') and name.endswith('.json'))

def path_leaks(file,forbidden):
    leaks=[];paths=[path.encode() for path in forbidden]+[b'/mnt/data/']
    with tarfile.open(file) as archive:
        for member in archive.getmembers():
            if not member.isfile():continue
            content=archive.extractfile(member).read()
            for path in paths:
                if path in content:leaks.append({'code':'ABSOLUTE_PATH_LEAK','member':member.name,'value':path.decode()})
            if metadata_member(member.name):
                for value in json_strings(json.loads(content)):
                    if HOST_ROOT.match(value):leaks.append({'code':'ABSOLUTE_PATH_IN_METADATA','member':member.name,'value':value})
    return leaks
