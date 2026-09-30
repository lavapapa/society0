import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("有实验资料时呈现主体机制、会话与对应组件", async () => {
  const fixture = {
    study: { title: "示例研究", question: "主体如何选择？" },
    versions: [{ id: "v1", name: "版本 1", config: { agents: [{ id: "agent-a", persona: "谨慎" }] }, entities: [{ id: "agent-a", name: "主体 A", kind: "llm-agent", group: "参与者", configPath: "/agents/0" }], runs: [{ id: "a", name: "条件 A", ticks: [{ id: "1", label: "Tick 1" }], snapshots: [{
      tickId: "1", entityId: "agent-a",
      mechanisms: [{
        id: "fov-a", name: "公开信息", access: "available",
        views: [{ id: "table", title: "记录", type: "table",
          columns: [{ key: "title", label: "标题" }], rows: [{ title: "观察一" }] }],
      }],
      sessions: [{ id: "s1", label: "第一次交互",
        events: [{ kind: "output", text: "选择结果" }] }],
    }] }] }],
  };
  globalThis.document = {
    getElementById(id) {
      return id === "society0-workbench-data" ? { textContent: JSON.stringify(fixture) } : null;
    },
  };
  const server = await createServer({
    configFile: false,
    plugins: [(await import("@vitejs/plugin-react")).default()],
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true },
    appType: "custom",
  });
  try {
    const { App } = await server.ssrLoadModule("/src/App.jsx");
    const html = renderToStaticMarkup(React.createElement(App));
    for (const expected of ["示例研究", "版本 1", "主体 A", "公开信息", "当前可接触", "观察一", "第一次交互", "选择结果", "复制变更"]) {
      assert.ok(html.includes(expected), expected);
    }
    const { ViewTabs } = await server.ssrLoadModule("/src/views.jsx");
    const renderView = (view) => renderToStaticMarkup(React.createElement(ViewTabs, { views: [view], context: {} }));
    const candle = renderView({ id: "ohlc", type: "candlestick",
      rows: [{ x: "1", open: 2, high: 4, low: 1, close: 3 }] });
    assert.ok(candle.includes("data-chart"));
    const network = renderView({ id: "ties", type: "network",
      nodes: [{ id: "a", label: "主体 A" }, { id: "b", label: "主体 B" }],
      edges: [{ from: "a", to: "b" }] });
    assert.ok(network.includes("network-chart"));
    assert.ok(network.includes("主体 B"));
    const table = renderView({ id: "many", type: "table",
      columns: [{ key: "label", label: "记录" }],
      rows: Array.from({ length: 10_000 }, (_, index) => ({ label: `记录 ${index}` })) });
    assert.ok(table.includes("共 10,000 条记录"));
    assert.ok(table.includes("记录 0"));
    assert.ok(!table.includes("记录 9999"));
    assert.ok(table.includes('aria-rowcount="10001"'));
    assert.ok(table.includes('aria-rowindex="2"'));
  } finally {
    await server.close();
    delete globalThis.document;
  }
});

test("尚无试运行时，工作台仍展示配置字段和待发送变更", async () => {
  const fixture = {
    study: { title: "配置研究", question: "如何改变信任？" },
    versions: [{ id: "v1", name: "初稿", configSource: "versions/v1/experiment.py",
      config: { agents: [{ id: "reader", persona: "谨慎读者" }], environment: { type: "plain" } },
      entities: [
        { id: "reader", name: "读者", kind: "llm-agent", configPath: "/agents/0" },
        { id: "environment", name: "环境", kind: "overview", configPath: "/environment" },
      ], runs: [] }],
  };
  globalThis.document = { getElementById: () => ({ textContent: JSON.stringify(fixture) }) };
  const server = await createServer({ configFile: false, plugins: [(await import("@vitejs/plugin-react")).default()],
    optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true }, appType: "custom" });
  try {
    const { App } = await server.ssrLoadModule("/src/App.jsx");
    const html = renderToStaticMarkup(React.createElement(App));
    for (const expected of ["配置研究", "初稿", "谨慎读者", "环境", "配置检查", "待发送变更", "本版本尚无试运行", "复制变更"]) {
      assert.ok(html.includes(expected), expected);
    }
    assert.ok(!html.includes("Agent 会话"));
    assert.ok(!html.includes("可接触的信息"));
  } finally {
    await server.close();
    delete globalThis.document;
  }
});
