import test from 'node:test';
import { createRequire } from 'node:module';
const installed = (() => { try { createRequire(import.meta.url).resolve('@society0/host'); return true; } catch { return false; } })();
if (!installed)
    test('INSTALLED_ONLY consumer', { skip: '需要安装态 @society0/host；请运行 scripts/acceptance.py 在仓外 consumer 中执行' }, () => {});
else
    await import('./consumer.installed.mjs');
