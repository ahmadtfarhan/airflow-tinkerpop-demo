#!/usr/bin/env python3
"""
Build the benchmark question set with gold answers.

Gold answers are computed in pure Python from the generated graph, so the
benchmark can be scored deterministically with set precision/recall/F1.
Five buckets (B1-B5) span attribute lookup through global aggregation and
unmodelled prose. Each question also carries the Gremlin traversal that
produces its answer, for verification by scripts/demo_doctor.py.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any, Dict, List, Set


class Graph:
    """Ground-truth adjacency built from the corpus's logical edges."""

    def __init__(self, entities: Dict[str, Any], edges: List[Dict[str, Any]]):
        self.ent = entities
        self.label = {k: v["label"] for k, v in entities.items()}
        self.name = {k: v["name"] for k, v in entities.items()}
        self.props = {k: v["props"] for k, v in entities.items()}
        self.link: Dict[str, Set[str]] = collections.defaultdict(set)
        self.access: Dict[str, Set[str]] = collections.defaultdict(set)
        self.member: Dict[str, Set[str]] = collections.defaultdict(set)
        self.grants: Dict[str, Set[str]] = collections.defaultdict(set)
        for e in edges:
            a, b, rt = e["a"], e["b"], e["relation_type"]
            if rt == "CUSTOMER_LINK":
                self.link[a].add(b)
                self.link[b].add(a)
            elif rt == "ACCESS":
                self.access[a].add(b)
            elif rt == "MEMBER":
                self.member[a].add(b)
            elif rt == "GRANTS":
                self.grants[a].add(b)

    def ids(self, label: str) -> List[str]:
        return sorted(k for k, v in self.label.items() if v == label)

    def network(self, c: str) -> Set[str]:
        """Customers reachable through CUSTOMER_LINK, including c itself."""
        seen, frontier = {c}, [c]
        while frontier:
            nxt = []
            for x in frontier:
                for y in self.link[x] - seen:
                    seen.add(y)
                    nxt.append(y)
            frontier = nxt
        return seen

    def hops(self, c: str) -> Dict[str, int]:
        dist, frontier = {c: 0}, [c]
        while frontier:
            nxt = []
            for x in frontier:
                for y in self.link[x]:
                    if y not in dist:
                        dist[y] = dist[x] + 1
                        nxt.append(y)
            frontier = nxt
        return dist

    def direct_resources(self, c: str) -> Set[str]:
        out = set(self.access[c])
        for g in self.member[c]:
            out |= self.grants[g]
        return out

    def reachable_resources(self, c: str) -> Set[str]:
        """Direct + inherited resources across the linked network."""
        out: Set[str] = set()
        for x in self.network(c):
            out |= self.direct_resources(x)
        return out

    def components(self) -> List[Set[str]]:
        seen: Set[str] = set()
        comps: List[Set[str]] = []
        for n in self.ids("Customer"):
            if n in seen or not self.link[n]:
                continue
            comp = self.network(n)
            seen |= comp
            comps.append(comp)
        return sorted(comps, key=len, reverse=True)

    def shortest_path(self, a: str, b: str) -> List[str]:
        prev: Dict[str, str] = {}
        dist, frontier = {a: 0}, [a]
        while frontier:
            nxt = []
            for x in frontier:
                for y in self.link[x]:
                    if y not in dist:
                        dist[y] = dist[x] + 1
                        prev[y] = x
                        nxt.append(y)
            frontier = nxt
        if b not in dist:
            return []
        path, cur = [b], b
        while cur != a:
            cur = prev[cur]
            path.append(cur)
        return list(reversed(path))


# Reusable Gremlin fragments. Chainable step sequences without a leading
# ``__.`` -- callers that need an anonymous traversal prefix it themselves.
NETWORK = ("union(__.identity(), __.repeat(__.bothE('RELATED_TO')"
           ".has('relation_type','CUSTOMER_LINK').otherV().simplePath())"
           ".emit().times(6)).dedup()")
RESOURCES_OF = ("union("
                "__.bothE('RELATED_TO').has('relation_type','ACCESS').otherV().hasLabel('Resource'),"
                "__.bothE('RELATED_TO').has('relation_type','MEMBER').otherV().hasLabel('Group')"
                ".bothE('RELATED_TO').has('relation_type','GRANTS').otherV().hasLabel('Resource'))")


def q(qid, bucket, question, gold_ids=None, gold_scalar=None, gremlin="", min_docs=1,
      note="", ordered=False):
    # Ordered answers (a path) must keep their order, not be sorted.
    ids = list(gold_ids or []) if ordered else sorted(gold_ids or [])
    return {
        "id": qid,
        "bucket": bucket,
        "question": question,
        "ordered": ordered,
        "gold_entity_ids": ids,
        "gold_scalar": gold_scalar,
        "gremlin": gremlin,
        "min_docs": min_docs,
        "note": note,
    }


def build(g: Graph, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    qs: List[Dict[str, Any]] = []
    nm = g.name

    customers = g.ids("Customer")
    resources = g.ids("Resource")
    confidential = [r for r in resources if g.props[r]["sensitivity"] == "Confidential"]

    by_degree = sorted(customers, key=lambda c: (-len(g.link[c]), c))
    hub = by_degree[0]
    with_access = sorted([c for c in customers if g.access[c]])
    with_member = sorted([c for c in customers if g.member[c]])
    comps = g.components()
    big = comps[0]

    # B1: attribute lookup, one document
    for i, c in enumerate(by_degree[:5] + with_access[:2]):
        p = g.props[c]
        qs.append(q(f"B1-{i+1:02d}", "B1",
                    f"What subscription tier and region is {nm[c]} on?",
                    gold_scalar=f"{p['tier']}, {p['region']}",
                    gremlin=f"g.V().hasLabel('Customer').has('id','{c}').valueMap('tier','region')",
                    min_docs=1))
    for j, r in enumerate(confidential[:3]):
        p = g.props[r]
        qs.append(q(f"B1-{len(qs)-len(qs)+8+j:02d}".replace("B1-", "B1-"), "B1",
                    f"What is the sensitivity classification of {nm[r]}?",
                    gold_scalar=p["sensitivity"],
                    gremlin=f"g.V().hasLabel('Resource').has('id','{r}').values('sensitivity')",
                    min_docs=1))

    # B2: two documents of different types
    n = 0
    for c in by_degree[:3]:
        n += 1
        partners = sorted(g.link[c])
        qs.append(q(f"B2-{n:02d}", "B2",
                    f"Which accounts are directly linked to {nm[c]}, and what tier is each on?",
                    gold_ids=partners,
                    gremlin=(f"g.V().hasLabel('Customer').has('id','{c}')"
                             ".bothE('RELATED_TO').has('relation_type','CUSTOMER_LINK')"
                             ".otherV().valueMap('id','tier')"),
                    min_docs=1 + len(partners),
                    note="link docs never state tier; profile docs never state links"))
    for c in with_member[:3]:
        n += 1
        inherited = sorted({r for gr in g.member[c] for r in g.grants[gr]})
        qs.append(q(f"B2-{n:02d}", "B2",
                    f"Which groups is {nm[c]} a member of, and which resources do those "
                    f"groups grant access to?",
                    gold_ids=sorted(g.member[c]) + inherited,
                    gremlin=(f"g.V().hasLabel('Customer').has('id','{c}')"
                             ".bothE('RELATED_TO').has('relation_type','MEMBER').otherV()"
                             ".hasLabel('Group').bothE('RELATED_TO').has('relation_type','GRANTS')"
                             ".otherV().hasLabel('Resource').values('id').dedup()"),
                    min_docs=1 + len(g.member[c]),
                    note="the inheritance hop that N_GROUP_GRANTS=0 used to delete"))
    ent_conf = sorted({c for c in customers
                       if g.props[c]["tier"] == "Enterprise"
                       and g.direct_resources(c) & set(confidential)})
    n += 1
    qs.append(q(f"B2-{n:02d}", "B2",
                "Which Enterprise-tier accounts hold access to a Confidential resource?",
                gold_ids=ent_conf,
                gremlin=("g.V().hasLabel('Customer').has('tier','Enterprise').as('c')"
                         f".where(__.{RESOURCES_OF}.has('sensitivity','Confidential'))"
                         ".select('c').values('id').dedup()"),
                min_docs=3))
    for r in confidential[:3]:
        n += 1
        holders = sorted({c for c in customers if r in g.direct_resources(c)})
        qs.append(q(f"B2-{n:02d}", "B2",
                    f"Who holds access to {nm[r]}, directly or through a group?",
                    gold_ids=holders,
                    gremlin=(f"g.V().hasLabel('Resource').has('id','{r}')"
                             ".bothE('RELATED_TO').has('relation_type','ACCESS')"
                             ".otherV().hasLabel('Customer').values('id').dedup()"),
                    min_docs=2))

    # B3: transitive closure
    n = 0
    # Pick subjects by distinct non-empty closure, not by component.
    subjects, signatures = [], set()
    for c in [hub] + [x for comp in comps for x in sorted(comp)]:
        closure = frozenset(g.reachable_resources(c))
        if not closure or closure in signatures:
            continue
        signatures.add(closure)
        subjects.append(c)
        if len(subjects) == 2:
            break
    for c in subjects:
        n += 1
        closure = g.reachable_resources(c)
        qs.append(q(f"B3-{n:02d}", "B3",
                    f"Through its network of linked accounts, which resources can "
                    f"{nm[c]} ultimately reach?",
                    gold_ids=sorted(closure),
                    gremlin=(f"g.V().hasLabel('Customer').has('id','{c}')"
                             f".{NETWORK}.{RESOURCES_OF}.values('id').dedup()"),
                    min_docs=10,
                    note="the headline question"))
    # Bounded-hop closure: same subject, shows traversal depth is a parameter.
    hops2 = {x for x, d in g.hops(hub).items() if d <= 2}
    near = sorted(set().union(*[g.direct_resources(x) for x in hops2]) if hops2 else set())
    if near and near != sorted(g.reachable_resources(hub)):
        n += 1
        qs.append(q(f"B3-{n:02d}", "B3",
                    f"Which resources can {nm[hub]} reach within two account-link hops?",
                    gold_ids=near,
                    gremlin=(f"g.V().hasLabel('Customer').has('id','{hub}')"
                             ".union(__.identity(), __.repeat(__.bothE('RELATED_TO')"
                             ".has('relation_type','CUSTOMER_LINK').otherV().simplePath())"
                             f".emit().times(2)).dedup().{RESOURCES_OF}.values('id').dedup()"),
                    min_docs=6))

    far = max(((len(g.hops(a)), max(g.hops(a).values()), a) for a in big))[2]
    target = max(g.hops(far).items(), key=lambda kv: (kv[1], kv[0]))[0]
    n += 1
    path = g.shortest_path(far, target)
    qs.append(q(f"B3-{n:02d}", "B3",
                f"What is the shortest chain of account links connecting {nm[far]} "
                f"and {nm[target]}?",
                gold_ids=path,
                ordered=True,
                gold_scalar=" -> ".join(nm[p] for p in path),
                gremlin=(f"g.V().hasLabel('Customer').has('id','{far}')"
                         ".repeat(__.bothE('RELATED_TO').has('relation_type','CUSTOMER_LINK')"
                         ".otherV().simplePath())"
                         f".until(__.has('id','{target}')).path().by('id').limit(1)"),
                min_docs=len(path) - 1,
                note="ordered answer: score exact match plus sequence edit distance"))
    n += 1
    qs.append(q(f"B3-{n:02d}", "B3",
                f"If {nm[far]} were compromised, which resources would be exposed "
                f"through its linked accounts?",
                gold_ids=sorted(g.reachable_resources(far)),
                gremlin=(f"g.V().hasLabel('Customer').has('id','{far}')"
                         f".{NETWORK}.{RESOURCES_OF}.values('id').dedup()"),
                min_docs=10))
    for r in [r for r in resources if len({c for c in customers
                                           if r in g.reachable_resources(c)}) > 3][:2]:
        n += 1
        qs.append(q(f"B3-{n:02d}", "B3",
                    f"Which accounts can reach {nm[r]}, directly or through linked accounts?",
                    gold_ids=sorted({c for c in customers if r in g.reachable_resources(c)}),
                    gremlin=(f"g.V().hasLabel('Resource').has('id','{r}')"
                             ".bothE('RELATED_TO').has('relation_type','ACCESS').otherV()"
                             f".hasLabel('Customer').{NETWORK}.values('id').dedup()"),
                    min_docs=8))
    n += 1
    free_conf = sorted({c for c in customers if g.props[c]["tier"] == "Free"
                        and g.reachable_resources(c) & set(confidential)})
    qs.append(q(f"B3-{n:02d}", "B3",
                "Which Free-tier accounts can transitively reach a Confidential resource?",
                gold_ids=free_conf,
                gremlin=("g.V().hasLabel('Customer').has('tier','Free').as('c')"
                         f".where(__.{NETWORK}.{RESOURCES_OF}.has('sensitivity','Confidential'))"
                         ".select('c').values('id').dedup()"),
                min_docs=12))

    # B4: global aggregation
    n = 0
    n += 1
    qs.append(q(f"B4-{n:02d}", "B4",
                "How many accounts are on the Enterprise tier?",
                gold_scalar=str(sum(1 for c in customers if g.props[c]["tier"] == "Enterprise")),
                gremlin="g.V().hasLabel('Customer').has('tier','Enterprise').count()",
                min_docs=70,
                note="requires reading every profile document"))
    n += 1
    qs.append(q(f"B4-{n:02d}", "B4",
                "Which account has the most direct links to other accounts?",
                gold_ids=[hub], gold_scalar=nm[hub],
                gremlin=("g.V().hasLabel('Customer').order()"
                         ".by(__.bothE('RELATED_TO').has('relation_type','CUSTOMER_LINK')"
                         ".count(), desc).limit(1).values('id')"),
                min_docs=25))
    n += 1
    qs.append(q(f"B4-{n:02d}", "B4",
                "How many separate, non-overlapping clusters of linked accounts exist?",
                gold_scalar=str(len(comps)),
                gremlin=("g.V().hasLabel('Customer').where(__.bothE('RELATED_TO')"
                         ".has('relation_type','CUSTOMER_LINK'))"
                         f".group().by(__.{NETWORK}.values('id').order().fold()).count(local)"),
                min_docs=25,
                note="structurally impossible without a graph"))
    n += 1
    best = max(resources, key=lambda r: (len({c for c in customers
                                              if r in g.reachable_resources(c)}), r))
    qs.append(q(f"B4-{n:02d}", "B4",
                "Which resource is reachable by the largest number of accounts?",
                gold_ids=[best], gold_scalar=nm[best],
                gremlin=("g.V().hasLabel('Resource').as('r').map("
                         "__.bothE('RELATED_TO').has('relation_type','ACCESS').otherV()"
                         f".hasLabel('Customer').{NETWORK}.dedup().count())"
                         ".order().by(desc).limit(1).select('r').values('id')"),
                min_docs=20))
    n += 1
    churned = sorted(c for c in customers if g.props[c]["status"] == "Churned")
    exposed = sorted(c for c in churned if g.reachable_resources(c) & set(confidential))
    qs.append(q(f"B4-{n:02d}", "B4",
                "Which Churned accounts still hold access to a Confidential resource?",
                gold_ids=exposed,
                gold_scalar=f"{len(exposed)} of {len(churned)}",
                gremlin=("g.V().hasLabel('Customer').has('status','Churned').as('c')"
                         f".where(__.{RESOURCES_OF}.has('sensitivity','Confidential'))"
                         ".select('c').values('id').dedup()"),
                min_docs=6,
                note="the compliance question"))
    for tier in ("Free", "Pro"):
        n += 1
        qs.append(q(f"B4-{n:02d}", "B4",
                    f"How many accounts are on the {tier} tier?",
                    gold_scalar=str(sum(1 for c in customers if g.props[c]["tier"] == tier)),
                    gremlin=f"g.V().hasLabel('Customer').has('tier','{tier}').count()",
                    min_docs=70))
    n += 1
    regions = collections.Counter(g.props[c]["region"] for c in customers)
    top_region = max(sorted(regions), key=lambda r: regions[r])
    qs.append(q(f"B4-{n:02d}", "B4",
                "Which region has the most accounts?",
                gold_scalar=top_region,
                gremlin="g.V().hasLabel('Customer').groupCount().by('region')",
                min_docs=70))
    n += 1
    qs.append(q(f"B4-{n:02d}", "B4",
                "How many resources are classified Confidential?",
                gold_scalar=str(len(confidential)),
                gremlin="g.V().hasLabel('Resource').has('sensitivity','Confidential').count()",
                min_docs=20))
    n += 1
    qs.append(q(f"B4-{n:02d}", "B4",
                "How many accounts sit in the largest cluster of linked accounts?",
                gold_scalar=str(len(big)),
                gremlin=("g.V().hasLabel('Customer').has('id','%s')" % hub + f".{NETWORK}.count()"),
                min_docs=25))

    # B5: prose the schema does not model. Graph F1 ~0 here by construction --
    # the graph agent correctly has no way to answer these, and should say so.
    # Gold is derived from ticket text (docs), not from crm_edges.csv, since
    # that is the only place these facts live.
    by_type: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for d in docs:
        by_type[d["doc_type"]].append(d)

    latency_accounts = sorted({e for d in by_type["distractor_latency"] for e in d["entities"]})
    qs.append(q("B5-01", "B5",
                "Which accounts reported that dashboards were slow to load?",
                gold_ids=latency_accounts,
                gremlin="", min_docs=1,
                note="only stated in distractor tickets; no schema models it"))

    near_miss_ids = sorted({e for d in by_type["near_miss"] for e in d["entities"]})
    qs.append(q("B5-02", "B5",
                "Which access requests were denied or withdrawn, and for which resources?",
                gold_ids=near_miss_ids,
                gremlin="", min_docs=1,
                note="the near-miss trap: reads like a grant, retrieves like a grant"))

    themes = sorted(t.removeprefix("distractor_") for t in by_type if t.startswith("distractor_"))
    qs.append(q("B5-03", "B5",
                "Summarise the recurring themes in support tickets across the account base.",
                gold_scalar=", ".join(themes),
                gremlin="", min_docs=10,
                note="scored on keyword coverage of the ticket categories, not phrasing"))

    billing_accounts = sorted({e for d in by_type["distractor_billing"] for e in d["entities"]})
    qs.append(q("B5-04", "B5",
                "Which accounts had a billing or invoicing query raised?",
                gold_ids=billing_accounts,
                gremlin="", min_docs=1))

    password_accounts = sorted({e for d in by_type["distractor_password"] for e in d["entities"]})
    qs.append(q("B5-05", "B5",
                "Were any password resets performed, and was any entitlement changed as part "
                "of them?",
                gold_ids=password_accounts, gold_scalar="no",
                gremlin="", min_docs=1,
                note="entitlement-change scalar answer is 'no' across every reset ticket"))
    return qs


def validate(questions: List[Dict[str, Any]], docs: List[Dict[str, Any]]) -> List[str]:
    """Flag degenerate questions: empty or near-universal gold sets, or
    multi-hop questions answerable from a single document."""
    problems: List[str] = []
    universe = sum(1 for _ in docs)
    for qn in questions:
        if qn["bucket"] == "B5":
            continue  # gold is sourced from ticket text, not the graph; corpus-share checks don't apply
        has_gold = bool(qn["gold_entity_ids"]) or qn["gold_scalar"] not in (None, "")
        if not has_gold:
            problems.append(f"{qn['id']}: empty gold answer (both arms 'win' by saying nothing)")
        if not qn.get("ordered") and len(qn["gold_entity_ids"]) > 0.5 * universe:
            problems.append(f"{qn['id']}: gold set is >50% of the corpus")
        if qn["bucket"] in ("B2", "B3") and qn["min_docs"] < 2:
            problems.append(f"{qn['id']}: {qn['bucket']} answerable from a single document")
        if qn["bucket"] != "B5" and not qn["gremlin"]:
            problems.append(f"{qn['id']}: no Gremlin traversal for verification")

    # Distinctness: several questions sharing one gold set narrows the bucket.
    for bucket in ("B2", "B3"):
        sets = [tuple(x["gold_entity_ids"]) for x in questions
                if x["bucket"] == bucket and x["gold_entity_ids"]]
        if sets and len(set(sets)) < max(2, len(sets) // 2):
            problems.append(
                f"{bucket}: only {len(set(sets))} distinct gold sets across "
                f"{len(sets)} questions -- subjects are too similar")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", default=None)
    args = ap.parse_args()

    corpus = Path(args.corpus_dir) if args.corpus_dir else \
        Path(__file__).resolve().parent.parent / "data" / "corpus"

    entities = json.loads((corpus / "entities.json").read_text())
    edges = json.loads((corpus / "gold_edges.json").read_text())
    docs = json.loads((corpus / "docs.json").read_text())

    g = Graph(entities, edges)
    questions = build(g, docs)
    problems = validate(questions, docs)

    (corpus / "questions.json").write_text(json.dumps(questions, indent=1), encoding="utf-8")

    counts = collections.Counter(qn["bucket"] for qn in questions)
    print(f"Wrote {len(questions)} questions to {corpus / 'questions.json'}")
    for b in sorted(counts):
        print(f"  {b}: {counts[b]}")
    if problems:
        print("\nDEGENERATE QUESTIONS:")
        for p in problems:
            print(f"  ! {p}")
        raise SystemExit(1)
    print("\nAll questions validated.")


if __name__ == "__main__":
    main()
