// 自定义面板：辩论议题—证据矩阵。
// 作为普通 React 组件挂到 window.SOCIETY0_PANELS，panel.type 写 custom，renderer 写这里注册的名字。
import React from 'react';

export function IssueMatrix({ dataset, select }) {
  if (!dataset) return <p className="muted">没有绑定资料。</p>;
  const rows = dataset.rows || [];
  return (
    <table className="issue-matrix">
      <thead>
        <tr><th>议题</th><th>支持证据</th><th>反例</th><th>状态</th></tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} onClick={() => row.entityId && select({ entityId: row.entityId, pageId: 'setup' })}>
            <td>{row.issue}</td>
            <td>{row.support}</td>
            <td>{row.counter}</td>
            <td>{row.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// 在页面数据生成后、渲染前注入：
// window.SOCIETY0_PANELS = { issueMatrix: IssueMatrix };
