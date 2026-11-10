/** 一次激活的 /tmp 快照；由 ExplorationShell 按会话持有，不进 world、不落盘。 */
export interface ShellTmpEntry {
    readonly path: string;
    readonly directory?: boolean;
    readonly data?: string;
}

export interface ShellTmpState {
    readonly entries: readonly ShellTmpEntry[];
}

/**
 * /tmp 总字节上限：1 MiB，与 exploration-shell 默认 maxFileBytes（1048576）一致，
 * 临时草稿不应比普通可读文件更大。
 */
export const TMP_MAX_BYTES = 1048576;

/**
 * /tmp 文件数上限：64，低于目录列举默认 maxDirectoryEntries（128），
 * 给一次激活的草稿留出足够空间，同时避免把临时区当成存储。
 */
export const TMP_MAX_FILES = 64;
