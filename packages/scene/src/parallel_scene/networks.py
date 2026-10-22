"""Graph connectivity checks for road, drainage, water and ventilation networks."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable


class Graph:
    def __init__(self, directed: bool = True):
        self.directed = directed
        self.adj: dict[str, set[str]] = defaultdict(set)
        self.nodes: set[str] = set()

    def add_node(self, n: str) -> None:
        self.nodes.add(n)

    def add_edge(self, a: str, b: str) -> None:
        self.nodes.update((a, b))
        self.adj[a].add(b)
        if not self.directed:
            self.adj[b].add(a)

    def reachable_from(self, start: str) -> set[str]:
        seen = {start}
        q = deque([start])
        while q:
            n = q.popleft()
            for m in self.adj.get(n, ()):
                if m not in seen:
                    seen.add(m)
                    q.append(m)
        return seen

    def reaches_any(self, start: str, targets: Iterable[str]) -> bool:
        r = self.reachable_from(start)
        return any(t in r for t in targets)

    def undirected_components(self) -> list[set[str]]:
        und: dict[str, set[str]] = defaultdict(set)
        for a, bs in self.adj.items():
            for b in bs:
                und[a].add(b)
                und[b].add(a)
        seen: set[str] = set()
        comps = []
        for n in sorted(self.nodes):
            if n in seen:
                continue
            comp = set()
            q = deque([n])
            seen.add(n)
            while q:
                x = q.popleft()
                comp.add(x)
                for y in und.get(x, ()):
                    if y not in seen:
                        seen.add(y)
                        q.append(y)
            comps.append(comp)
        return comps
