import io
import tarfile
import pytest

from benchmarks.core_next_deployment import verify


def archive(tmp_path):
    path=tmp_path/'source.tar'
    with tarfile.open(path,'w') as stream:
        for name,raw in [('src/a.py',b'original'),('tests/t.py',b'test'),('uv.lock',b'locked')]:
            item=tarfile.TarInfo(name);item.size=len(raw);stream.addfile(item,io.BytesIO(raw))
    target=tmp_path/'source'
    with tarfile.open(path) as stream:stream.extractall(target,filter='data')
    return path,target


def test_exact_deployment_and_cache_ignored(tmp_path):
    path,target=archive(tmp_path)
    (target/'src/__pycache__').mkdir();(target/'src/__pycache__/a.pyc').write_bytes(b'cache')
    assert verify(path,target)=={'files':3,'bytes':18,'equal':True}


@pytest.mark.parametrize('mutation',['changed','extra','missing'])
def test_deployment_difference_rejected(tmp_path,mutation):
    path,target=archive(tmp_path)
    if mutation=='changed':(target/'src/a.py').write_bytes(b'changed')
    elif mutation=='extra':(target/'tests/old.py').write_bytes(b'old')
    else:(target/'uv.lock').unlink()
    with pytest.raises(ValueError):verify(path,target)
