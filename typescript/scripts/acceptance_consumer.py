"""仓外消费者资产与公开元数据锁；安装验收与安装态测试共用。"""
from pathlib import Path
import json, shutil


def installed_plugin_names(consumer):
    names = []
    for path in sorted((consumer / 'node_modules/@society0').glob('*/package.json')):
        package = json.loads(path.read_text())
        if package.get('society', {}).get('plugin', {}).get('metadata'):
            names.append(package['name'])
    return names


def prepare_installed_assets(inputs, consumer):
    # import.meta.resolve 从脚本位置寻找包，示例须置于消费者目录内。
    shutil.copytree(inputs / 'examples', consumer, dirs_exist_ok=True)
    shutil.copytree(inputs / 'profiles', consumer / 'profiles')


def profile_lock_command(consumer, mode):
    flags = ['--allow-dirty'] if mode == 'dirty-development' else []
    return ['node', str(consumer / 'profile-lock.mjs'), *flags, str(consumer / 'module-lock.json'), *installed_plugin_names(consumer)]
