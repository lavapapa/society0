"""检查已生成站点的内部页面、资源、锚点及未替换构建标记。"""
import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.ids = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for key in ('href', 'src'):
            if key in attrs:
                self.links.append((tag, key, attrs[key]))


def check(root):
    root = root.resolve()
    pages = {}
    failures = []
    for path in root.rglob('*.html'):
        text = path.read_text()
        pages[path] = Page(text)
        if '@COMMIT@' in text or '<!-- api:' in text:
            failures.append(f'{path.relative_to(root)}: 存在未展开构建标记')
    checked = 0
    for path, page in pages.items():
        for tag, key, raw in page.links:
            url = urlsplit(raw)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if target.is_dir():
                target /= 'index.html'
            checked += 1
            if not target.exists():
                failures.append(f'{path.relative_to(root)}: 缺少 {raw}')
            elif tag == 'a' and url.fragment and target in pages:
                if unquote(url.fragment) not in pages[target].ids:
                    failures.append(f'{path.relative_to(root)}: 无锚点 {raw}')
    assert pages, '没有生成HTML页面'
    if failures:
        raise AssertionError('\n'.join(failures))
    print(f'站点检查通过：{len(pages)} HTML页面，{checked}处内部页面、资源与锚点引用')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('site', type=Path)
    check(parser.parse_args().site)
