"""发行与产物验收共用的绝对路径门禁。"""
import json,re,tarfile,itertools
from pathlib import Path
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

# cases: RV3-01-T05
def members(file):
    with tarfile.open(file) as archive:
        return {member.name:{'type':member.type.decode(),'mode':member.mode,'size':member.size,'mtime':member.mtime,
                'content':archive.extractfile(member).read() if member.isfile() else None} for member in archive.getmembers()}

def native_differences(a,b):
    differences=({'offset':index,'a':left,'b':right} for index,(left,right) in enumerate(itertools.zip_longest(a,b),1) if left!=right)
    return list(itertools.islice(differences,50))

def member_content(member):
    return (member or {}).get('content') or b''

def member_metadata(member):
    return {key:value for key,value in (member or {}).items() if key!='content'}

def member_detail(name,a,b):
    left=member_content(a);right=member_content(b)
    detail={'aPrefix':list(left[:200]),'bPrefix':list(right[:200]),
            'aMetadata':member_metadata(a),
            'bMetadata':member_metadata(b)}
    if name.endswith(('.node','.so')):detail['nativeByteDifferences']=native_differences(left,right)
    return detail

def archive_report(a,b):
    left=members(a);right=members(b)
    differences=sorted(name for name in left.keys()|right.keys() if left.get(name)!=right.get(name))
    equal=a.read_bytes()==b.read_bytes()
    return {'name':a.name,'archiveEqual':equal,'differentMembers':differences,
            'reason':None if equal or differences else 'ARCHIVE_ONLY',
            'memberDetails':{name:member_detail(name,left.get(name),right.get(name)) for name in differences}}

def differs(report):
    return bool(report['pathLeaks']) or any(not row['archiveEqual'] or row['differentMembers'] for row in report['tarballs'])

def compare(a,b,forbidden=()):
    a=Path(a);b=Path(b);names=sorted(file.name for file in a.glob('*.tgz'));other=sorted(file.name for file in b.glob('*.tgz'))
    report={'status':'identical','tarballs':[],'pathLeaks':[]}
    if names!=other or len(names)!=13:return {**report,'status':'different','reason':'TARBALL_SET','a':names,'b':other}
    for name in names:
        report['tarballs'].append(archive_report(a/name,b/name))
        for side,directory in [('a',a),('b',b)]:
            report['pathLeaks'].extend({**leak,'side':side,'tarball':name} for leak in path_leaks(directory/name,forbidden))
    if differs(report):report['status']='different'
    return report
