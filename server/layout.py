# SPDX-License-Identifier: MIT
"""Graph layout for board_add kind:"flow" (09 plan B6) — Python standard library only (runs on 3.9).

keep     the agent read node centres (`at`) off a snapshot of a person's sketch; keep them (people remember their own layout) and only tidy:
         x on a 20 px grid, nodes whose y differ by less than 30 % of the smaller height share one row y.
layered  longest-path layers (cycles broken by reversing DFS back edges) + 2-4 barycentre sweeps; one cell per node, so boxes never overlap.

Both take node sizes and return centres {id: (cx, cy)}; the caller builds the elements.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

GRID = 20.0
ROW_FRACTION = 0.3     # keep: |dy| < 30 % of the smaller height → same row
KEEP_MAX_SHIFT = 20.0  # keep: a node centre never moves further than this from its `at` because of the grid
LAYER_GAP = 90.0       # layered: gap between layers (along the flow direction)
NODE_GAP = 60.0        # layered: gap between nodes in one layer
SWEEPS = 4
MAX_NODES = 50

Size = Tuple[float, float]
Centre = Tuple[float, float]


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def keep(at: Dict[str, Centre], sizes: Dict[str, Size]) -> Dict[str, Centre]:
    """Tidy the given centres without moving any more than KEEP_MAX_SHIFT (20 px):
    rows chain — a node joins the row of the node above it when their y differ by less than ROW_FRACTION of the smaller height; one row y is
    chosen within 20 px of every member (a row spread over more than 40 px is split at its largest gap, since no single y can serve it);
    x goes on the 20 px grid only when that keeps the whole move within 20 px."""
    out: Dict[str, Centre] = {}
    order = sorted(at, key=lambda k: (at[k][1], at[k][0]))
    rows: List[List[str]] = []
    for k in order:
        if rows:
            prev = rows[-1][-1]
            if abs(at[k][1] - at[prev][1]) < ROW_FRACTION * min(sizes[k][1], sizes[prev][1]):
                rows[-1].append(k)
                continue
        rows.append([k])
    done: List[List[str]] = []
    while rows:
        row = rows.pop()
        ys = [at[k][1] for k in row]
        if max(ys) - min(ys) <= 2 * KEEP_MAX_SHIFT:
            done.append(row)
            continue
        gaps = [(ys[i + 1] - ys[i], i) for i in range(len(row) - 1)]   # row is sorted by y
        i = max(gaps)[1]
        rows += [row[:i + 1], row[i + 1:]]
    for row in done:
        ys = [at[k][1] for k in row]
        lo, hi = max(ys) - KEEP_MAX_SHIFT, min(ys) + KEEP_MAX_SHIFT
        mean = sum(ys) / len(ys)
        y = _snap(mean)
        if not lo <= y <= hi:
            y = min(max(mean, lo), hi)
        for k in row:
            x0, y0 = at[k]
            x = _snap(x0)
            if (x - x0) ** 2 + (y - y0) ** 2 > KEEP_MAX_SHIFT ** 2:
                x = x0
            out[k] = (x, y)
    return out


def _break_cycles(ids: Sequence[str], edges: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Edges of a DAG: DFS in input order, an edge into a node still on the stack is reversed."""
    adj: Dict[str, List[str]] = {k: [] for k in ids}
    for a, b in edges:
        adj[a].append(b)
    state = {k: 0 for k in ids}   # 0 new, 1 on stack, 2 done
    back = set()
    for root in ids:
        if state[root]:
            continue
        stack = [(root, iter(adj[root]))]
        state[root] = 1
        while stack:
            v, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[v] = 2
                stack.pop()
            elif state[nxt] == 1:
                back.add((v, nxt))
            elif state[nxt] == 0:
                state[nxt] = 1
                stack.append((nxt, iter(adj[nxt])))
    return [(b, a) if (a, b) in back else (a, b) for a, b in edges]


def layers(ids: Sequence[str], edges: Sequence[Tuple[str, str]]) -> Dict[str, int]:
    """Longest-path layer of each node (sources at 0)."""
    dag = _break_cycles(ids, edges)
    pos = {k: i for i, k in enumerate(ids)}
    preds: Dict[str, List[str]] = {k: [] for k in ids}
    indeg = {k: 0 for k in ids}
    for a, b in dag:
        preds[b].append(a)
        indeg[b] += 1
    succ: Dict[str, List[str]] = {k: [] for k in ids}
    for a, b in dag:
        succ[a].append(b)
    ready = sorted((k for k in ids if indeg[k] == 0), key=pos.get)
    lay: Dict[str, int] = {}
    while ready:
        v = ready.pop(0)
        lay[v] = max((lay[p] + 1 for p in preds[v]), default=0)
        for w in succ[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                ready.append(w)
                ready.sort(key=pos.get)
    return lay


def layered(ids: Sequence[str], edges: Sequence[Tuple[str, str]], sizes: Dict[str, Size], direction: str = "TD") -> Dict[str, Centre]:
    """Centres for a layered drawing. direction "TD" (layers go down) or "LR" (layers go right)."""
    return layered_routes(ids, edges, sizes, direction)[0]


def layered_routes(ids: Sequence[str], edges: Sequence[Tuple[str, str]], sizes: Dict[str, Size],
                   direction: str = "TD") -> Tuple[Dict[str, Centre], List[List[Centre]]]:
    """Layered drawing with bend points: an edge spanning more than one layer (a reversed back edge too) gets one dummy slot per layer it
    crosses, ordered with the real nodes, so its polyline goes around the boxes instead of through them.
    -> (centres of the real nodes, per edge the bend points from its `from` end to its `to` end — [] for edges between neighbouring layers)."""
    if not ids:
        return {}, [[] for _ in edges]
    lay = layers(ids, edges)
    dag = _break_cycles(ids, edges)
    nodes = list(ids)
    lay_all = dict(lay)
    chains: List[List[str]] = []
    links: List[Tuple[str, str]] = []
    for i, ((a, b), (u, v)) in enumerate(zip(edges, dag)):
        chain = []
        for L in range(lay_all[u] + 1, lay_all[v]):
            d = "\0d%d_%d" % (i, L)   # cannot clash with a node id from JSON
            nodes.append(d)
            lay_all[d] = L
            chain.append(d)
        path = [u] + chain + [v]
        links += list(zip(path, path[1:]))
        chains.append(chain if (a, b) == (u, v) else chain[::-1])
    n = max(lay_all.values()) + 1
    rows: List[List[str]] = [[] for _ in range(n)]
    for k in nodes:
        rows[lay_all[k]].append(k)
    nbrs: Dict[str, List[str]] = {k: [] for k in nodes}
    for a, b in links:
        nbrs[a].append(b)
        nbrs[b].append(a)
    for s in range(SWEEPS):
        down = s % 2 == 0
        idx = {k: i for row in rows for i, k in enumerate(row)}
        seq = range(1, n) if down else range(n - 2, -1, -1)
        for L in seq:
            def bary(k, L=L):
                ns = [idx[m] for m in nbrs[k] if (lay_all[m] < L if down else lay_all[m] > L)]
                return sum(ns) / len(ns) if ns else idx[k]
            rows[L] = sorted(rows[L], key=lambda k: (bary(k), idx[k]))
            idx.update({k: i for i, k in enumerate(rows[L])})
    cw = max(sizes[k][0] for k in ids)
    ch = max(sizes[k][1] for k in ids)
    pos: Dict[str, Centre] = {}
    for L, row in enumerate(rows):
        for i, k in enumerate(row):
            off = i - (len(row) - 1) / 2
            if direction == "LR":
                pos[k] = (L * (cw + LAYER_GAP) + cw / 2, off * (ch + NODE_GAP))
            else:
                pos[k] = (off * (cw + NODE_GAP), L * (ch + LAYER_GAP) + ch / 2)
    # an edge between neighbouring layers that runs backwards, or repeats a pair already drawn, would lie exactly on the other one
    # (labels on top of each other): give it one bend point in the gap between the layers, pushed sideways, alternating sides
    seen: Dict[frozenset, int] = {}
    via = [[pos[d] for d in c] for c in chains]
    for i, ((a, b), (u, v)) in enumerate(zip(edges, dag)):
        if chains[i]:
            continue
        key = frozenset((a, b))
        k = seen.get(key, 0)
        seen[key] = k + 1
        if k == 0 and (a, b) == (u, v):
            continue   # the first forward edge of a pair stays straight
        step = (k // 2 + 1) * (1 if k % 2 == 0 else -1)   # k-th repeat (or a back edge): +1, -1, +2, -2, …
        mx, my = (pos[a][0] + pos[b][0]) / 2, (pos[a][1] + pos[b][1]) / 2
        if direction == "LR":
            via[i] = [(mx, my + step * (ch / 2 + 30))]
        else:
            via[i] = [(mx + step * (cw / 2 + 30), my)]
    return {k: pos[k] for k in ids}, via
