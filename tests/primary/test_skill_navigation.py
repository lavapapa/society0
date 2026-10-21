"""独立安装包的导航消费者；流程质量交给任务演练判断。"""
from pathlib import Path
import re
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2] / 'skill'
GUIDES = ('SKILL.md', *(str(path.relative_to(ROOT)) for path in sorted((ROOT / 'references').glob('*.md'))))


@pytest.mark.parametrize('relative', GUIDES)
def test_workflow_links_resolve_inside_installed_skill(relative):
    document = ROOT / relative
    text = document.read_text()
    for href in re.findall(r'\]\(<?([^\s)>]+)>?\)', text):
        if href.startswith(('https://', 'http://', '#', 'mailto:')):
            continue
        target = (document.parent / unquote(href.split('#')[0])).resolve()
        assert target.is_relative_to(ROOT.resolve()), (relative, href)
        assert target.is_file(), (relative, href)


def test_all_reference_leaves_are_discoverable_from_entry():
    entry = (ROOT / 'SKILL.md').read_text()
    for leaf in (ROOT / 'references').glob('*.md'):
        assert f'references/{leaf.name}' in entry, leaf.name
