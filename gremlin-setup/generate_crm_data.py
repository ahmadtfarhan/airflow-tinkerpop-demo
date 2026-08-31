#!/usr/bin/env python3
"""
Generate a CRM-ish graph dataset for Gremlin/TinkerPop (single relationship
type). ~100 vertices (70 Customer, 20 Resource, 10 Group); all edges use
label RELATED_TO, written bidirectionally, with relation_type distinguishing
CUSTOMER_LINK / ACCESS / MEMBER / GRANTS semantics. Deterministic with seed.
"""

from __future__ import annotations
import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple


SEED = 42

N_CUSTOMERS = 70
N_RESOURCES = 20
N_GROUPS = 10

# Relationship volumes (logical links, before doubling for bidirectional).
# N_CUSTOMER_LINKS=25 yields 6 components sized [19,4,2,2,2,2], diameter 7
# in the largest -- raising it makes the graph denser and questions easier.
N_CUSTOMER_LINKS = 25     # Customer<->Customer logical links
N_ACCESS_LINKS = 20       # Customer<->Resource logical links
N_MEMBERSHIPS = 10        # Customer<->Group logical links
N_GROUP_GRANTS = 8        # Group<->Resource logical links


@dataclass(frozen=True)
class Vertex:
    vid: str
    label: str
    name: str
    props: Dict[str, object]


@dataclass(frozen=True)
class Edge:
    out_id: str
    in_id: str
    label: str
    props: Dict[str, object]


def _pick_unique_pairs(
    rng: random.Random,
    candidates: List[Tuple[str, str]],
    k: int
) -> List[Tuple[str, str]]:
    k = min(k, len(candidates))
    rng.shuffle(candidates)
    return candidates[:k]


def _add_bidirectional_edge(
    edges: List[Edge],
    seen_directed: Set[Tuple[str, str, str, Tuple[Tuple[str, object], ...]]],
    a: str,
    b: str,
    label: str,
    props: Dict[str, object],
) -> None:
    """Add A->B and B->A, deduplicating exact directed edges."""
    props_items = tuple(sorted(props.items(), key=lambda x: x[0]))

    k1 = (a, b, label, props_items)
    if k1 not in seen_directed:
        edges.append(Edge(out_id=a, in_id=b, label=label, props=props))
        seen_directed.add(k1)

    k2 = (b, a, label, props_items)
    if k2 not in seen_directed:
        edges.append(Edge(out_id=b, in_id=a, label=label, props=props))
        seen_directed.add(k2)



# Applied as a deterministic post-pass after the rng draws, so the random
# stream and every other generated value stay untouched.
CONFIDENTIAL_EVERY = 4


def _apply_sensitivity_floor(vertices: List[Vertex]) -> List[Vertex]:
    """Force every Nth Resource to Confidential."""
    out: List[Vertex] = []
    seen = 0
    for v in vertices:
        if v.label == "Resource":
            seen += 1
            if seen % CONFIDENTIAL_EVERY == 0:
                props = dict(v.props)
                props["sensitivity"] = "Confidential"
                v = Vertex(vid=v.vid, label=v.label, name=v.name, props=props)
        out.append(v)
    return out


def build_vertices(rng: random.Random) -> List[Vertex]:
    """Build all vertices in a fixed rng order."""
    vertices: List[Vertex] = []

    for i in range(1, N_CUSTOMERS + 1):
        tier = rng.choices(["Free", "Pro", "Enterprise"], weights=[55, 35, 10], k=1)[0]
        region = rng.choice(["UK", "EU", "US", "MENA"])
        status = rng.choices(["Active", "Churned", "Prospect"], weights=[75, 10, 15], k=1)[0]
        vertices.append(
            Vertex(
                vid=f"C{i:03d}",
                label="Customer",
                name=f"Customer {i:03d}",
                props={"tier": tier, "region": region, "status": status},
            )
        )

    resource_types = ["Dashboard", "Report", "Dataset", "Project", "Folder"]
    for i in range(1, N_RESOURCES + 1):
        rtype = rng.choice(resource_types)
        sensitivity = rng.choices(["Public", "Internal", "Confidential"], weights=[40, 40, 20], k=1)[0]
        vertices.append(
            Vertex(
                vid=f"R{i:03d}",
                label="Resource",
                name=f"{rtype} {i:03d}",
                props={"resource_type": rtype, "sensitivity": sensitivity},
            )
        )

    for i in range(1, N_GROUPS + 1):
        vertices.append(
            Vertex(
                vid=f"G{i:03d}",
                label="Group",
                name=f"Group {i:03d}",
                props={"role": rng.choice(["Admin", "Analyst", "Viewer", "Support"])},
            )
        )

    return _apply_sensitivity_floor(vertices)


def build_edges(rng: random.Random, vertices: List[Vertex]) -> List[Edge]:
    """Build all edges. Single label RELATED_TO, discriminated by relation_type."""
    customer_ids = [v.vid for v in vertices if v.label == "Customer"]
    resource_ids = [v.vid for v in vertices if v.label == "Resource"]
    group_ids = [v.vid for v in vertices if v.label == "Group"]

    edges: List[Edge] = []
    seen_directed: Set[Tuple[str, str, str, Tuple[Tuple[str, object], ...]]] = set()
    perms = ["READ", "WRITE", "ADMIN"]

    # 1) Customer<->Customer links.
    cust_pairs_undirected = [
        (a, b) for i, a in enumerate(customer_ids) for b in customer_ids[i + 1 :]
    ]
    chosen_cc = _pick_unique_pairs(rng, cust_pairs_undirected, N_CUSTOMER_LINKS)

    rel_types = ["CONNECTED_TO", "REFERRED", "SAME_ORG_AS", "INFLUENCED_BY"]
    for a, b in chosen_cc:
        _add_bidirectional_edge(
            edges, seen_directed, a, b, "RELATED_TO",
            props={
                "relation_type": "CUSTOMER_LINK",
                "sub_type": rng.choice(rel_types),
                "strength": rng.randint(1, 5),
            },
        )

    # 2) Customer<->Resource direct access.
    cr_pairs = [(c, r) for c in customer_ids for r in resource_ids]
    for c, r in _pick_unique_pairs(rng, cr_pairs, N_ACCESS_LINKS):
        _add_bidirectional_edge(
            edges, seen_directed, c, r, "RELATED_TO",
            props={
                "relation_type": "ACCESS",
                "permission": rng.choices(perms, weights=[70, 25, 5], k=1)[0],
            },
        )

    # 3) Customer<->Group membership.
    cg_pairs = [(c, g) for c in customer_ids for g in group_ids]
    for c, g in _pick_unique_pairs(rng, cg_pairs, N_MEMBERSHIPS):
        _add_bidirectional_edge(
            edges, seen_directed, c, g, "RELATED_TO",
            props={"relation_type": "MEMBER", "since_days": rng.randint(1, 900)},
        )

    # 4) Group<->Resource grants (the inheritance hop).
    gr_pairs = [(g, r) for g in group_ids for r in resource_ids]
    for g, r in _pick_unique_pairs(rng, gr_pairs, N_GROUP_GRANTS):
        _add_bidirectional_edge(
            edges, seen_directed, g, r, "RELATED_TO",
            props={
                "relation_type": "GRANTS",
                "permission": rng.choices(perms, weights=[60, 30, 10], k=1)[0],
            },
        )

    _plant_compliance_scenario(edges, vertices)
    return edges


def _plant_compliance_scenario(edges: List[Edge], vertices: List[Vertex]) -> None:
    """Guarantee "Churned accounts with Confidential access" has a real
    answer: one direct grant, one inherited via MEMBER -> Group -> GRANTS.
    Does not touch CUSTOMER_LINK, so component structure is unaffected."""
    props = {v.vid: v.props for v in vertices}
    label = {v.vid: v.label for v in vertices}
    seen: Set[Tuple[str, str, str, Tuple[Tuple[str, object], ...]]] = {
        (e.out_id, e.in_id, e.label, tuple(sorted(e.props.items()))) for e in edges
    }

    churned = sorted(
        v for v in props if label[v] == "Customer" and props[v]["status"] == "Churned"
    )
    confidential = sorted(
        v for v in props if label[v] == "Resource" and props[v]["sensitivity"] == "Confidential"
    )
    groups = sorted(v for v in props if label[v] == "Group")
    if len(churned) < 2 or len(confidential) < 2 or not groups:
        return

    # 1) Direct exposure.
    _add_bidirectional_edge(
        edges, seen, churned[0], confidential[0], "RELATED_TO",
        props={"relation_type": "ACCESS", "permission": "READ"},
    )

    # 2) Inherited exposure: reuse or create a Confidential group grant.
    granting = sorted(
        {
            e.out_id
            for e in edges
            if e.props["relation_type"] == "GRANTS"
            and label[e.out_id] == "Group"
            and e.in_id in confidential
        }
    )
    group = granting[0] if granting else groups[0]
    if not granting:
        _add_bidirectional_edge(
            edges, seen, group, confidential[1], "RELATED_TO",
            props={"relation_type": "GRANTS", "permission": "READ"},
        )
    _add_bidirectional_edge(
        edges, seen, churned[1], group, "RELATED_TO",
        props={"relation_type": "MEMBER", "since_days": 365},
    )


def build_graph(seed: int = SEED) -> Tuple[List[Vertex], List[Edge]]:
    """Deterministic ground-truth graph."""
    rng = random.Random(seed)
    vertices = build_vertices(rng)
    edges = build_edges(rng, vertices)
    return vertices, edges


def _props_str(props: Dict[str, object]) -> str:
    return ";".join(f"{k}={props[k]}" for k in sorted(props))


def write_csvs(
    vertices: List[Vertex],
    edges: List[Edge],
    base_dir: Path | None = None,
) -> Tuple[Path, Path]:
    base_dir = base_dir or Path(__file__).resolve().parent
    vertices_path = base_dir / "crm_vertices.csv"
    edges_path = base_dir / "crm_edges.csv"

    with vertices_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "label", "name", "props"])
        for v in vertices:
            w.writerow([v.vid, v.label, v.name, _props_str(v.props)])

    with edges_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["out_id", "in_id", "label", "props"])
        for e in edges:
            w.writerow([e.out_id, e.in_id, e.label, _props_str(e.props)])

    return vertices_path, edges_path


def main() -> None:
    vertices, edges = build_graph()
    vertices_path, edges_path = write_csvs(vertices, edges)
    print(f"Wrote vertices: {vertices_path}")
    print(f"Wrote edges:    {edges_path}")
    print(
        f"Counts: vertices={len(vertices)} edges={len(edges)} "
        "(directed edges; includes bidirectional pairs)"
    )


if __name__ == "__main__":
    main()
