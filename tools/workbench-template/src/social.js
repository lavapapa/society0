export function socialFor(version, run, tickId) {
  return run ? run.socialSnapshots?.find(frame => frame.tickId === tickId) || null : version?.social || null;
}

export function actorActivity(frame, id) {
  const posts = frame.posts || [];
  return {
    posts: posts.filter(p => p.author === id).length,
    likesGiven: posts.every(p => Array.isArray(p.likes))
      ? posts.filter(p => p.likes.includes(id)).length : null,
  };
}

export function filterNetwork(frame, {types, group='', ego=null}) {
  const nodes=(frame.nodes || []).filter(n => !group || n.group === group || n.kind === 'post');
  const ids=new Set(nodes.map(n=>n.id));
  const edges=(frame.edges || []).filter(e=>types.includes(e.type) && ids.has(e.source) && ids.has(e.target));
  if (!ego) return {nodes,edges};
  const linked=edges.filter(e=>e.source===ego || e.target===ego);
  const near=new Set([ego,...linked.flatMap(e=>[e.source,e.target])]);
  return {nodes:nodes.filter(n=>near.has(n.id)),edges:linked};
}

export function filterPosts(frame, {actor='',mode='authored',query='',sort='newest'}) {
  const names=Object.fromEntries((frame.nodes || []).map(n=>[n.id,n.label || n.id]));
  const feed=new Set(frame.feeds?.[actor] || []);
  const needle=query.trim().toLowerCase();
  return (frame.posts || []).filter(p=>(!actor || (mode==='feed'?feed.has(p.id):p.author===actor)) &&
    `${p.content} ${p.title || ''} ${p.author} ${names[p.author] || ''}`.toLowerCase().includes(needle))
    .sort((a,b)=>sort==='oldest'?a.tick-b.tick:sort==='replies'?(b.replies?.length || 0)-(a.replies?.length || 0):b.tick-a.tick);
}
