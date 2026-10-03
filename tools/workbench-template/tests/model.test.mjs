import assert from "node:assert/strict";
import { test } from "node:test";
import {
  blankStudy,
  snapshotFor,
  mainTabsFor,
  rightTabsFor,
  visibleRows,
  getAtPath,
  resolveConfigPath,
  setAtPath,
  changesBetween,
  changeRequest,
} from "../src/model.js";

const study = {
  ...blankStudy,
  versions: [{ id: "v1", name: "版本 1", config: { agents: [{ id: "reader", temperature: 0.4 }], environment: { tax: 0.1 } }, entities: [
    { id: "reader", name: "读者", kind: "llm-agent" },
    { id: "council", name: "议会", kind: "institution" },
  ], runs: [{ id: "a", ticks: [{ id: "1" }, { id: "2" }], snapshots: [
    {
      tickId: "1", entityId: "reader",
      mechanisms: [{ id: "feed", name: "推荐信息", access: "available", views: [] }],
      sessions: [{ id: "s1" }, { id: "s2" }],
      tabs: [{ id: "outcomes", title: "结果", modules: [] }],
      sideTabs: [{ id: "events", title: "事件", modules: [] }],
    },
    { tickId: "1", entityId: "council", tabs: [{ id: "policy", title: "政策", modules: [] }] },
    { tickId: "2", entityId: "reader", mechanisms: [{ id: "feed", access: "unknown" }] },
  ] }] }],
};

test("空模板没有虚构的主体、机制或记录", () => {
  assert.equal(blankStudy.versions.length, 0);
});

test("按运行、tick 和主体精确选择资料", () => {
  assert.equal(snapshotFor(study, "v1", "a", "1", "reader").sessions.length, 2);
  assert.equal(snapshotFor(study, "v1", "a", "2", "reader").mechanisms[0].access, "unknown");
  assert.equal(snapshotFor(study, "v1", "a", "2", "council"), null);
  assert.equal(snapshotFor(study, "v1", "b", "1", "reader"), null);
  assert.equal(snapshotFor(study, "v2", "a", "1", "reader"), null);
});

test("LLM 主体默认看机制与会话；制度实体只出现有资料的标签", () => {
  const agent = snapshotFor(study, "v1", "a", "1", "reader");
  assert.deepEqual(mainTabsFor(agent, study.versions[0].entities[0], true).map((x) => x.id), ["mechanisms", "config", "outcomes"]);
  assert.deepEqual(rightTabsFor(agent, study.versions[0].entities[0], true).map((x) => x.id), ["sessions", "changes", "events"]);
  const institution = snapshotFor(study, "v1", "a", "1", "council");
  assert.deepEqual(mainTabsFor(institution, study.versions[0].entities[1], false).map((x) => x.id), ["policy"]);
  assert.deepEqual(rightTabsFor(institution, study.versions[0].entities[1], false), []);
});

test("配置可按路径修改，并把差异整理为待发送请求", () => {
  const before = study.versions[0].config;
  const after = setAtPath(before, "/agents/0/temperature", 0.7);
  assert.equal(getAtPath(after, "/agents/0/temperature"), 0.7);
  assert.equal(getAtPath(before, "/agents/0/temperature"), 0.4);
  const changes = changesBetween(before, after);
  assert.deepEqual(changes, [{ op: "replace", path: "/agents/0/temperature", before: 0.4, after: 0.7 }]);
  assert.equal(changeRequest(study.versions[0], changes).baseVersionId, "v1");
  assert.equal(changeRequest(study.versions[0], changes).changes.length, 1);
  assert.deepEqual(changesBetween({ a: 1 }, { b: 2 }), [
    { op: "remove", path: "/a", before: 1 },
    { op: "add", path: "/b", after: 2 },
  ]);
});

test("主体数组增删时按 id 定位，删掉的主体不能错指其他配置", () => {
  const original = { agents: [{ id: "a", persona: "甲" }, { id: "b", persona: "乙" }] };
  const reordered = { agents: [original.agents[1], original.agents[0]] };
  assert.equal(resolveConfigPath(original, reordered, "/agents/0"), "/agents/1");
  assert.equal(resolveConfigPath(original, { agents: [original.agents[1]] }, "/agents/0"), null);
  assert.equal(resolveConfigPath(original, reordered, "/agents/1"), "/agents/0");
});

test("虚拟列表只返回视口附近的行，边界不会越界", () => {
  assert.deepEqual(visibleRows(10_000, 0, 400, 40, 3), { start: 0, end: 13 });
  assert.deepEqual(visibleRows(10_000, 39_600, 400, 40, 3), { start: 987, end: 1003 });
  assert.deepEqual(visibleRows(100, 159, 260, 40, 3), { start: 0, end: 14 });
  assert.deepEqual(visibleRows(4, 1000, 400, 40, 3), { start: 0, end: 4 });
});
