"""同一冻结测试在build和实际安装包之间的公开入口重写。"""
import re

def rewrite_installed(name, text, rules):
    if rules == 'regex-public':
        text = text.replace('../.build/host/dist/profile-loader/compiler.js', '@society0/host/profile-loader')
        text = text.replace('../.build/core/dist/sqlite-history/history.js', '@society0/core/sqlite-history')
        text = re.sub(r"\.\./\.build/(host|core|extras|social)/dist/([^\"']+)\.js", lambda match: '@society0/' + match[1] + ('' if match[2] == 'index' else '/' + match[2].removesuffix('/index')), text)
    elif rules == 'random-native':
        text = re.sub(r"new URL\('\.\./packages/random-native/([^']+)',import\.meta\.url\)", lambda match: "new URL('./" + match[1] + "',pathToFileURL(require.resolve('@society0/random-native/package.json')))", text)
        text = re.sub(r"\.\./\.build/(host|core|extras|social)/dist/([^\"']+)\.js", lambda match: '@society0/' + match[1] + ('' if match[2] == 'index' else '/' + match[2].removesuffix('/index')), text)
        text = text.replace("fileURLToPath(new URL('../.build',import.meta.url))", "resolve('.')")
        text = re.sub(r"new URL\('\.\./\.build/random-native/([^']+)',import.meta.url\)", lambda match: "new URL('./" + match[1] + "',pathToFileURL(require.resolve('@society0/random-native/package.json')))", text)
    else:
        for source, target in rules.items():
            assert source in text, (name, source)
            text = text.replace(source, target)
    assert '../.build/' not in text, name
    return text
