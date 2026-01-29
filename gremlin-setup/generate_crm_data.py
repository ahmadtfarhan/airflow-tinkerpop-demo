#!/usr/bin/env python3
"""
Generate a CRM-ish graph dataset for Gremlin/TinkerPop (single relationship type).

Vertices (~100 total):
- Customer: 70
- Resource: 20
- Group: 10

Edges:
- All edges are label: RELATED_TO
- Bidirectional by writing two directed edges for each logical connection.
- relation_type property distinguishes semantics:
  - CUSTOMER_LINK (Customer <-> Customer)
  - ACCESS        (Customer <-> Resource)
  - MEMBER        (Customer <-> Group)
  - GRANTS        (Group <-> Resource)

Deterministic with seed.
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

# Relationship volumes (logical links, before doubling for bidirectional)
N_CUSTOMER_LINKS = 25     # Customer<->Customer logical links
N_ACCESS_LINKS = 20       # Customer<->Resource logical links
N_MEMBERSHIPS = 10        # Customer<->Group logical links
N_GROUP_GRANTS = 0       # Group<->Resource logical links


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
    """
    Add A->B and B->A, deduplicating exact directed edges.
    """
    # Make props hashable for dedupe key (order-independent)
    props_items = tuple(sorted(props.items(), key=lambda x: x[0]))

    k1 = (a, b, label, props_items)
    if k1 not in seen_directed:
        edges.append(Edge(out_id=a, in_id=b, label=label, props=props))
        seen_directed.add(k1)

    k2 = (b, a, label, props_items)
    if k2 not in seen_directed:
        edges.append(Edge(out_id=b, in_id=a, label=label, props=props))
        seen_directed.add(k2)


def main() -> None:
    rng = random.Random(SEED)
    base_dir = Path(__file__).resolve().parent
    vertices_path = base_dir / "crm_vertices.csv"
    edges_path = base_dir / "crm_edges.csv"

    # -------------------------
    # Build vertices
    # -------------------------
    vertices: List[Vertex] = []

    for i in range(1, N_CUSTOMERS + 1):
        vid = f"C{i:03d}"
        tier = rng.choices(["Free", "Pro", "Enterprise"], weights=[55, 35, 10], k=1)[0]
        region = rng.choice(["UK", "EU", "US", "MENA"])
        status = rng.choices(["Active", "Churned", "Prospect"], weights=[75, 10, 15], k=1)[0]
        vertices.append(
            Vertex(
                vid=vid,
                label="Customer",
                name=f"Customer {i:03d}",
                props={"tier": tier, "region": region, "status": status},
            )
        )

    resource_types = ["Dashboard", "Report", "Dataset", "Project", "Folder"]
    for i in range(1, N_RESOURCES + 1):
        vid = f"R{i:03d}"
        rtype = rng.choice(resource_types)
        sensitivity = rng.choices(["Public", "Internal", "Confidential"], weights=[40, 40, 20], k=1)[0]
        vertices.append(
            Vertex(
                vid=vid,
                label="Resource",
                name=f"{rtype} {i:03d}",
                props={"resource_type": rtype, "sensitivity": sensitivity},
            )
        )

    for i in range(1, N_GROUPS + 1):
        vid = f"G{i:03d}"
        vertices.append(
            Vertex(
                vid=vid,
                label="Group",
                name=f"Group {i:03d}",
                props={"role": rng.choice(["Admin", "Analyst", "Viewer", "Support"])},
            )
        )

    customer_ids = [v.vid for v in vertices if v.label == "Customer"]
    resource_ids = [v.vid for v in vertices if v.label == "Resource"]
    group_ids = [v.vid for v in vertices if v.label == "Group"]

    # -------------------------
    # Build edges (single label, bidirectional)
    # -------------------------
    edges: List[Edge] = []
    seen_directed: Set[Tuple[str, str, str, Tuple[Tuple[str, object], ...]]] = set()

    # 1) Customer<->Customer links (logical undirected pairs)
    # Use undirected candidate pairs (a<b) to avoid duplicates, then add bidirectional edges.
    cust_pairs_undirected = []
    for i, a in enumerate(customer_ids):
        for b in customer_ids[i + 1 :]:
            cust_pairs_undirected.append((a, b))

    chosen_cc = _pick_unique_pairs(rng, cust_pairs_undirected, N_CUSTOMER_LINKS)

    rel_types = ["CONNECTED_TO", "REFERRED", "SAME_ORG_AS", "INFLUENCED_BY"]
    for a, b in chosen_cc:
        _add_bidirectional_edge(
            edges,
            seen_directed,
            a,
            b,
            "RELATED_TO",
            props={
                "relation_type": "CUSTOMER_LINK",
                "sub_type": rng.choice(rel_types),
                "strength": rng.randint(1, 5),
            },
        )

    # 2) Customer<->Resource access links
    cr_pairs = [(c, r) for c in customer_ids for r in resource_ids]
    chosen_cr = _pick_unique_pairs(rng, cr_pairs, N_ACCESS_LINKS)

    perms = ["READ", "WRITE", "ADMIN"]
    for c, r in chosen_cr:
        _add_bidirectional_edge(
            edges,
            seen_directed,
            c,
            r,
            "RELATED_TO",
            props={
                "relation_type": "ACCESS",
                "permission": rng.choices(perms, weights=[70, 25, 5], k=1)[0],
            },
        )

    # 3) Customer<->Group membership links
    cg_pairs = [(c, g) for c in customer_ids for g in group_ids]
    chosen_cg = _pick_unique_pairs(rng, cg_pairs, N_MEMBERSHIPS)

    for c, g in chosen_cg:
        _add_bidirectional_edge(
            edges,
            seen_directed,
            c,
            g,
            "RELATED_TO",
            props={
                "relation_type": "MEMBER",
                "since_days": rng.randint(1, 900),
            },
        )

    # 4) Group<->Resource grants links
    gr_pairs = [(g, r) for g in group_ids for r in resource_ids]
    chosen_gr = _pick_unique_pairs(rng, gr_pairs, N_GROUP_GRANTS)

    for g, r in chosen_gr:
        _add_bidirectional_edge(
            edges,
            seen_directed,
            g,
            r,
            "RELATED_TO",
            props={
                "relation_type": "GRANTS",
                "permission": rng.choices(perms, weights=[60, 30, 10], k=1)[0],
            },
        )

    # -------------------------
    # Write CSVs
    # -------------------------
    with vertices_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "label", "name", "props"])
        for v in vertices:
            props_str = ";".join([f"{k}={v.props[k]}" for k in sorted(v.props.keys())])
            w.writerow([v.vid, v.label, v.name, props_str])

    with edges_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["out_id", "in_id", "label", "props"])
        for e in edges:
            props_str = ";".join([f"{k}={e.props[k]}" for k in sorted(e.props.keys())])
            w.writerow([e.out_id, e.in_id, e.label, props_str])

    print(f"Wrote vertices: {vertices_path}")
    print(f"Wrote edges:    {edges_path}")
    print(f"Counts: vertices={len(vertices)} edges={len(edges)} (directed edges; includes bidirectional pairs)")


if __name__ == "__main__":
    main()
