"""初始化用 NetworkX 拓扑；迭代期间的关系权威由社交 SQL 表持有。"""
import random
import networkx as nx
from .social_models import SocialNetworkConfig


def cv(graph,node):
    outgoing=set(graph.successors(node)); incoming=set(graph.predecessors(node))
    union=outgoing|incoming
    return len(outgoing&incoming)/len(union) if union else 0.0


def _traditional(members,config,rng):
    graph=nx.DiGraph();graph.add_nodes_from(members)
    n=len(members); distribution=config.distribution;params=distribution.params
    if not n:return graph
    if distribution.type=='random':
        edges=nx.gnp_random_graph(n,params.connection_probability,directed=config.is_directed,seed=rng).edges()
    elif distribution.type=='small_world':
        edges=nx.watts_strogatz_graph(n,min(n-1,params.k_neighbors),params.rewiring_probability,seed=rng).edges()
    elif distribution.type=='scale_free':
        edges=nx.barabasi_albert_graph(n,min(n-1,params.m_edges),seed=rng).edges() if n>1 else []
    elif distribution.type=='complete':edges=nx.complete_graph(n).edges()
    else:edges=[]
    for a,b in edges:
        graph.add_edge(members[a],members[b])
        if not config.is_directed:graph.add_edge(members[b],members[a])
    return graph


def generate_topology(members,config,*,seed=0):
    """全图初始化的计算量由节点、边及显式 CV 迭代次数决定。"""
    members=tuple(members);rng=random.Random(seed)
    if config.distribution.type!='cv_targeted':return _traditional(members,config,rng)
    graph=nx.DiGraph();graph.add_nodes_from(members)
    if len(members)<2:return graph
    params=config.distribution.params
    kind=params.base_algorithm
    distribution={'type':kind,'params':params.base_params}
    if kind not in ('small_world','scale_free','random'):distribution={'type':'small_world'}
    base=SocialNetworkConfig(distribution=distribution,is_directed=True)
    graph=_traditional(members,base,rng)
    for _ in range(params.max_iterations):
        current=sum(cv(graph,node) for node in members)/len(members)
        if abs(current-params.target_cv_mean)<params.convergence_threshold:break
        if current<params.target_cv_mean:
            choices=[(a,b) for a,b in graph.edges if not graph.has_edge(b,a)]
            if choices:
                a,b=rng.choice(choices);graph.add_edge(b,a)
            else:
                choices=[(a,b) for i,a in enumerate(members) for b in members[i+1:] if not graph.has_edge(a,b) and not graph.has_edge(b,a)]
                if choices:
                    a,b=rng.choice(choices);graph.add_edge(a,b);graph.add_edge(b,a)
        else:
            choices=[(a,b) for a,b in graph.edges if graph.has_edge(b,a)]
            if choices:
                a,b=rng.choice(choices)
                if rng.random()<0.5:graph.remove_edge(b,a)
                else:graph.remove_edge(a,b)
            else:
                choices=[(a,b) for a in members for b in members if a!=b and not graph.has_edge(a,b)]
                if choices:graph.add_edge(*rng.choice(choices))
    return graph
