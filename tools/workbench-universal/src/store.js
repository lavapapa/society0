import { useCallback, useEffect, useMemo, useState } from 'react';
import { findVersion, normalize, runsOf, worldsOfVersion } from './data.js';

const STORAGE_KEY_PREFIX = 'society0-workbench';

function storageKey(studyId) {
  return `${STORAGE_KEY_PREFIX}:${studyId || 'unknown'}`;
}

function readStored(studyId) {
  try {
    const text = window.localStorage.getItem(storageKey(studyId));
    return text ? JSON.parse(text) : {};
  } catch {
    return {};
  }
}

function writeStored(studyId, value) {
  try {
    window.localStorage.setItem(storageKey(studyId), JSON.stringify(value));
  } catch {
    /* 浏览器禁用持久存储时继续以当前页面状态工作。 */
  }
}

/** 草稿以 study + 配置来源为键；源值变化时不覆盖新资料，交给界面提示冲突。 */
function draftKey(studyId, source) {
  return `${studyId}::${source || 'unknown'}`;
}

export function useWorkbench(raw) {
  const data = useMemo(() => normalize(raw), [raw]);
  const stored = useMemo(() => readStored(data.study.id), [data.study.id]);

  const [versionId, setVersionId] = useState(() => {
    const candidate = stored.selection?.versionId;
    return data.versions.some((item) => item.id === candidate) ? candidate : data.versions[0]?.id || '';
  });
  const [runId, setRunId] = useState(() => stored.selection?.runId || '');
  const [worldId, setWorldId] = useState(() => stored.selection?.worldId || '');
  const [observationId, setObservationId] = useState(() => stored.selection?.observationId || '');
  const [entityId, setEntityId] = useState(() => stored.selection?.entityId || '');
  const [pluginId, setPluginId] = useState(() => stored.selection?.pluginId || '');
  const [pageId, setPageId] = useState(() => stored.selection?.pageId || '');
  const [sectionId, setSectionId] = useState(() => stored.selection?.sectionId || 'plugins');
  const [collapsed, setCollapsed] = useState(() => Boolean(stored.ui?.collapsed));
  const [drafts, setDrafts] = useState(() => stored.drafts || {});
  const [conflicts, setConflicts] = useState([]);

  const version = useMemo(() => findVersion(data, versionId), [data, versionId]);
  const runs = useMemo(() => runsOf(data, version?.id), [data, version?.id]);
  const worlds = useMemo(() => worldsOfVersion(data, version?.id), [data, version?.id]);

  useEffect(() => {
    writeStored(data.study.id, {
      selection: { versionId, runId, worldId, observationId, entityId, pluginId, pageId, sectionId },
      ui: { collapsed },
      drafts,
    });
  }, [data.study.id, versionId, runId, worldId, observationId, entityId, pluginId, pageId, sectionId, collapsed, drafts]);

  useEffect(() => {
    if (runId && !runs.some((run) => run.id === runId)) setRunId('');
    if (worldId && !worlds.some((world) => world.id === worldId)) setWorldId('');
    if (entityId && !data.entities.some((entity) => entity.id === entityId)) setEntityId('');
  }, [runs, worlds, data.entities, runId, worldId, entityId]);

  const draftFor = useCallback(
    (source) => drafts[draftKey(data.study.id, source)],
    [drafts, data.study.id],
  );

  const setDraft = useCallback(
    (source, value) => {
      const key = draftKey(data.study.id, source);
      setDrafts((current) => ({ ...current, [key]: { value, base: value?.base ?? null, savedAt: new Date().toISOString() } }));
    },
    [data.study.id],
  );

  const clearDraft = useCallback(
    (source) => {
      const key = draftKey(data.study.id, source);
      setDrafts((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    },
    [data.study.id],
  );

  const select = useCallback((patch) => {
    if ('versionId' in patch) setVersionId(patch.versionId);
    if ('runId' in patch) setRunId(patch.runId);
    if ('worldId' in patch) setWorldId(patch.worldId);
    if ('observationId' in patch) setObservationId(patch.observationId);
    if ('entityId' in patch) setEntityId(patch.entityId);
    if ('pluginId' in patch) setPluginId(patch.pluginId);
    if ('pageId' in patch) setPageId(patch.pageId);
    if ('sectionId' in patch) setSectionId(patch.sectionId);
  }, []);

  return {
    data,
    version,
    runs,
    worlds,
    selection: { versionId, runId, worldId, observationId, entityId, pluginId, pageId, sectionId },
    collapsed,
    setCollapsed,
    select,
    draftFor,
    setDraft,
    clearDraft,
    conflicts,
    setConflicts,
  };
}
