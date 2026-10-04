"""从固定 Git 提交归档；部署后直接比较源码字节，不生成指纹。"""
import argparse
import json
from pathlib import Path
import subprocess
import tarfile

ROOTS=('src','tests','native','skill/assets','examples/core_next')
FILES=('pyproject.toml','uv.lock','benchmarks/core_next_deployment.py')


def selected(name):
    parts=Path(name).parts
    return bool(parts) and '__pycache__' not in parts and not name.endswith('.pyc') and (any(name.startswith(root+'/') for root in ROOTS) or name in FILES)


def verify(archive,destination):
    destination=Path(destination)
    expected=set();total=0
    with tarfile.open(archive) as source:
        for item in source:
            if not item.isfile() or not selected(item.name):continue
            expected.add(item.name)
            target=destination/item.name
            if not target.is_file():raise ValueError('deployment missing: '+item.name)
            with source.extractfile(item) as left,target.open('rb') as right:
                while raw:=left.read(65536):
                    total+=len(raw)
                    if raw!=right.read(len(raw)):raise ValueError('deployment differs: '+item.name)
                if right.read(1):raise ValueError('deployment differs: '+item.name)
    actual={p.relative_to(destination).as_posix() for root in ROOTS for p in (destination/root).rglob('*') if p.is_file() and selected(p.relative_to(destination).as_posix())}
    actual.update(name for name in FILES if (destination/name).is_file())
    if actual!=expected:raise ValueError('deployment file set differs: '+repr(sorted(actual^expected)))
    return {'files':len(expected),'bytes':total,'equal':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',required=True)
    parser.add_argument('--commit',help='固定且已推送的提交；提供时创建归档')
    parser.add_argument('--destination',help='核对已解包的部署目录')
    args=parser.parse_args();report={}
    if args.commit:
        commit=subprocess.check_output(['git','rev-parse',args.commit+'^{commit}'],text=True).strip()
        branch=subprocess.check_output(['git','branch','--show-current'],text=True).strip()
        reference='refs/heads/'+branch
        pushed=subprocess.check_output(['git','ls-remote','origin',reference],text=True).split()
        if len(pushed)!=2 or pushed[0]!=commit:raise ValueError('commit differs from the published branch HEAD')
        with open(args.archive,'xb') as out:subprocess.run(['git','archive',commit],stdout=out,check=True)
        report.update(commit=commit,remote_ref=reference,published_head_equal=True)
    if args.destination:report.update(verify(args.archive,args.destination))
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
