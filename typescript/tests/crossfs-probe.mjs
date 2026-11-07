import { mkdir, mkdtemp } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/** 跨文件系统测试前置：显式 PR05_CROSSFS，缺省回落到 W/.crossfs（ceph），不使用 /dev/shm。 */
export async function crossfsBase() {
    const configured = process.env.PR05_CROSSFS;
    if (configured) {
        if (!/\/society0-next-crossfs-[^/]+$/.test(configured))
            throw Error('CROSSFS_ENV_INVALID: PR05_CROSSFS 必须指向 W/.crossfs 下的 society0-next-crossfs-* 目录，实际 ' + configured);
        return dirname(configured);
    }
    const base = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../.crossfs');
    await mkdir(base, { recursive: true });
    return base;
}

export async function crossfsRoot() {
    const configured = process.env.PR05_CROSSFS;
    if (configured) {
        if (!/\/society0-next-crossfs-[^/]+$/.test(configured))
            throw Error('CROSSFS_ENV_INVALID: PR05_CROSSFS 必须指向 W/.crossfs 下的 society0-next-crossfs-* 目录，实际 ' + configured);
        return configured;
    }
    return mkdtemp(join(await crossfsBase(), 'society0-next-crossfs-'));
}
