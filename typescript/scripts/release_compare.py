"""发行与产物验收共用的绝对路径门禁。"""
import json,re,tarfile
HOST_ROOT=re.compile(r'^/(mnt|home|tmp|private|Users|root|opt|var|srv|data|nfs|scratch)(/|$)')

def config_pointer(trail):
    # 该字段声明配置项的JSON pointer，值中的/root指向配置键。
    return len(trail)==6 and trail[0]=='entries' and trail[2:4]==('metadata','configPolicy') and trail[5]=='path'

def json_strings(value,trail=()):
    if isinstance(value,str):
        if not config_pointer(trail):yield value
    elif isinstance(value,dict):
        for key,item in value.items():yield from json_strings(item,trail+(key,))
    elif isinstance(value,list):
        for index,item in enumerate(value):yield from json_strings(item,trail+(index,))

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
