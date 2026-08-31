#!/usr/bin/env python3
"""
Generate the text corpus the demo is built on. The CRM graph is implied by
the documents, never stated as a table, and each document states exactly
one atomic fact so multi-hop answers require joining across documents.
Deterministic (SEED=42), zero LLM calls.

Outputs (into data/corpus/):
    docs.json        JSON array of {doc_id, doc_type, text, entities, asserts}
    entities.json    canonical entities with their properties and display names
    aliases.json     every surface form -> canonical id
    gold_edges.json  the full edge list, used to score extraction quality
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List

from generate_crm_data import SEED, build_graph

# Documents use human names, not IDs, to force real entity normalization
# and stop lexical shortcutting on a verbatim ID string.
COMPANY_STEMS = [
    "Northwind", "Ironbark", "Blue Harbor", "Cedar Point", "Halcyon", "Meridian",
    "Kestrel", "Lantern", "Redwood", "Silverline", "Tidewater", "Vantage",
    "Wexford", "Aldergrove", "Brightwater", "Coppersmith", "Dunmore", "Eastgate",
    "Fernbank", "Glasshouse", "Hollowell", "Inverness", "Juniper", "Kingsley",
    "Larkspur", "Marlowe", "Nightingale", "Oakhaven", "Pinecrest", "Quarry Lane",
    "Ravensworth", "Stonebridge", "Thornfield", "Underhill", "Verity", "Whitfield",
    "Yarrow", "Ashford", "Belmont", "Chandler", "Dovetail", "Elmwood",
    "Foxglove", "Greyloch", "Hartwell", "Ivyford", "Jasperton", "Kirkwall",
    "Linden", "Moorcroft", "Netherby", "Orchard Row", "Pemberton", "Quillon",
    "Rosslare", "Saltmarsh", "Templeton", "Ulverston", "Vinehall", "Westbrook",
    "Wildergate", "Ambleside", "Bexhill", "Corvus", "Drakemoor", "Everly",
    "Fairholm", "Grendon", "Hawkridge", "Isolde", "Jarrow", "Kelmscott",
]
COMPANY_SUFFIXES = [
    "Analytics", "Logistics", "Systems", "Partners", "Holdings", "Labs",
    "Retail Group", "Industries", "Media", "Consulting",
]
RESOURCE_TOPICS = [
    "Quarterly Revenue", "Customer Churn", "Payroll", "Supply Chain",
    "Marketing Spend", "Incident Postmortem", "Board Pack", "Pricing Model",
    "Headcount Plan", "Vendor Contracts", "Product Telemetry", "Fraud Signals",
    "Regional Sales", "Support Backlog", "Capacity Forecast", "Audit Trail",
    "Partner Pipeline", "Churn Risk", "Licence Inventory", "Security Review",
]
GROUP_SCOPES = ["EMEA", "AMER", "APAC", "Global", "UK", "Nordics", "Iberia",
                "Benelux", "DACH", "MENA"]
GROUP_COLLECTIVES = ["Guild", "Working Group", "Circle", "Council", "Chapter"]

# Fixed display names for two reference entities.
PINNED_NAMES = {"C059": "Northwind Analytics", "R005": "Quarterly Revenue Report"}


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------
def build_names(vertices, rng: random.Random) -> Dict[str, str]:
    """Assign a unique human-readable display name to every entity."""
    names: Dict[str, str] = {}

    pool = [f"{stem} {suf}" for stem in COMPANY_STEMS for suf in COMPANY_SUFFIXES]
    rng.shuffle(pool)
    customers = [v for v in vertices if v.label == "Customer"]
    seen: set[str] = set()
    it = iter(pool)
    for v in customers:
        name = next(it)
        while name in seen:
            name = next(it)
        seen.add(name)
        names[v.vid] = name

    for i, v in enumerate(v for v in vertices if v.label == "Resource"):
        names[v.vid] = f"{RESOURCE_TOPICS[i % len(RESOURCE_TOPICS)]} {v.props['resource_type']}"

    for i, v in enumerate(v for v in vertices if v.label == "Group"):
        scope = GROUP_SCOPES[i % len(GROUP_SCOPES)]
        names[v.vid] = f"{scope} {v.props['role']} {rng.choice(GROUP_COLLECTIVES)}"

    # Swap so pinned entities get their fixed names without disturbing others.
    for vid, wanted in PINNED_NAMES.items():
        if vid not in names or names[vid] == wanted:
            continue
        holder = next((k for k, n in names.items() if n == wanted), None)
        if holder is not None:
            names[holder], names[vid] = names[vid], wanted
        else:
            names[vid] = wanted
    return names


def build_aliases(vertices, names: Dict[str, str]) -> Dict[str, str]:
    """Every surface form a document might use -> canonical id."""
    prefix = {"Customer": "ACC", "Resource": "RES", "Group": "GRP"}
    aliases: Dict[str, str] = {}
    for v in vertices:
        full = names[v.vid]
        aliases[full.casefold()] = v.vid
        aliases[v.vid.casefold()] = v.vid
        aliases[f"{prefix[v.label]}-{v.vid[1:]}".casefold()] = v.vid
        if v.label == "Customer":
            # short form only when unambiguous
            short = full.rsplit(" ", 1)[0]
            aliases.setdefault(short.casefold(), v.vid)
    return aliases


# --------------------------------------------------------------------------
# Document builders.  Each returns text plus the facts it is allowed to state.
# --------------------------------------------------------------------------
def _doc(doc_id, doc_type, text, entities, asserts) -> Dict[str, Any]:
    return {
        "doc_id": doc_id,
        "doc_type": doc_type,
        "text": " ".join(text.split()),
        "entities": entities,
        "asserts": asserts,
    }


def account_profile(n, vid, props, name, rng):
    body = rng.choice([
        f"""Account review note for {name}. The account is registered in the {props['region']}
        region and currently sits on our {props['tier']} plan. Account status is {props['status']}.
        The CSM confirmed the billing contact is unchanged this quarter and no plan change has
        been requested. Filed for the regional book of record.""",
        f"""Onboarding summary: {name}. Region of record is {props['region']}. Subscription
        tier is {props['tier']}. Present lifecycle status: {props['status']}. No outstanding
        provisioning items. This note captures account metadata only; entitlements are tracked
        separately by the access team.""",
        f"""CRM sync for {name}. Region {props['region']}, tier {props['tier']}, status
        {props['status']}. Record reconciled against the billing system with no discrepancies.
        Contact details were verified by phone.""",
    ])
    return _doc(f"DOC-{n:04d}", "account_profile", body, [vid], [
        {"kind": "attr", "subject": vid, "key": "region", "value": props["region"]},
        {"kind": "attr", "subject": vid, "key": "tier", "value": props["tier"]},
        {"kind": "attr", "subject": vid, "key": "status", "value": props["status"]},
    ])


def resource_catalog(n, vid, props, name, rng):
    body = rng.choice([
        f"""Data catalogue entry: {name}. Asset class is {props['resource_type']}. The
        information governance team classifies this asset as {props['sensitivity']}. Owners must
        review the classification annually. This entry records the asset itself; who may see it
        is held in the entitlement register.""",
        f"""Catalogue registration for {name}, a {props['resource_type']} asset.
        Classification: {props['sensitivity']}. Retention follows the standard schedule for its
        class. No consumers are listed on this record by design.""",
        f"""Asset sheet -- {name}. Type: {props['resource_type']}. Sensitivity tier assigned by
        governance: {props['sensitivity']}. Last classification review passed without change.""",
    ])
    return _doc(f"DOC-{n:04d}", "resource_catalog", body, [vid], [
        {"kind": "attr", "subject": vid, "key": "resource_type", "value": props["resource_type"]},
        {"kind": "attr", "subject": vid, "key": "sensitivity", "value": props["sensitivity"]},
    ])


def group_charter(n, vid, props, name, rng):
    body = rng.choice([
        f"""Charter: {name}. The group operates with the {props['role']} role profile. Its remit
        is set annually by the governance board. Membership is administered separately and is not
        recorded in this charter.""",
        f"""Terms of reference for {name}. Role profile: {props['role']}. The group meets
        fortnightly. Joiners and leavers are handled through the access request process rather
        than this document.""",
    ])
    return _doc(f"DOC-{n:04d}", "group_charter", body, [vid], [
        {"kind": "attr", "subject": vid, "key": "role", "value": props["role"]},
    ])


REL_PHRASING = {
    "SAME_ORG_AS": "are part of the same parent organisation",
    "CONNECTED_TO": "operate as connected accounts",
    "REFERRED": "came to us through a referral between the two",
    "INFLUENCED_BY": "share a buying committee, and one clearly influences the other",
}


def relationship_doc(n, a, b, name_a, name_b, props, rng):
    """States exactly one customer-customer link."""
    phrase = REL_PHRASING[props["sub_type"]]
    body = rng.choice([
        f"""Call summary. Spoke with the account team covering {name_a}. They confirmed that
        {name_a} and {name_b} {phrase}. The relationship has been in place for some time and the
        team rates its commercial strength at {props['strength']} out of 5. Action: reflect the
        link on both account records. No entitlement changes were discussed on this call.""",
        f"""Ticket: account linkage query. The requester asked us to confirm the tie between
        {name_a} and {name_b}. Verified with the regional lead -- the two {phrase}. Strength of
        the relationship is recorded as {props['strength']} of 5. Closing as confirmed.""",
        f"""Referral note. {name_b} was discussed alongside {name_a} at this week's pipeline
        review. The two {phrase}, which is why they are reviewed together. Relationship strength
        currently {props['strength']}/5. Filed against both accounts.""",
    ])
    return _doc(f"DOC-{n:04d}", "relationship", body, [a, b], [
        {"kind": "edge", "relation_type": "CUSTOMER_LINK", "a": a, "b": b,
         "sub_type": props["sub_type"], "strength": props["strength"]},
    ])


def access_doc(n, c, r, name_c, name_r, permission, rng):
    """States exactly one customer-resource grant."""
    body = rng.choice([
        f"""Access request -- APPROVED. {name_c} requested access to {name_r}. The data owner
        signed off and the entitlement has been provisioned at {permission} level. The requester
        completed the handling training before the grant was issued. Ticket closed.""",
        f"""Entitlement change confirmation. {name_c} now holds {permission} access to
        {name_r}, effective immediately. Provisioned by the access team following owner approval.
        Review date set per the standard cycle.""",
        f"""Email thread summary: {name_c} asked to be added to {name_r}. Approved. Permission
        level granted: {permission}. No other assets were included in this request.""",
    ])
    return _doc(f"DOC-{n:04d}", "access", body, [c, r], [
        {"kind": "edge", "relation_type": "ACCESS", "a": c, "b": r, "permission": permission},
    ])


def membership_doc(n, c, g, name_c, name_g, rng):
    """States exactly one customer-group membership."""
    body = rng.choice([
        f"""Ticket: add to working group. {name_c} has been enrolled as a member of {name_g}
        following approval from the group chair. Enrolment is recorded against the account.
        Any entitlements that follow from membership are administered by the group itself.""",
        f"""Membership confirmation. {name_c} joined {name_g} this cycle. The chair confirmed
        the seat. This note records membership only.""",
    ])
    return _doc(f"DOC-{n:04d}", "membership", body, [c, g], [
        {"kind": "edge", "relation_type": "MEMBER", "a": c, "b": g},
    ])


def grant_doc(n, g, r, name_g, name_r, permission, rng):
    """States exactly one group-resource grant."""
    body = rng.choice([
        f"""Governance memo. {name_g} has been granted {permission} access to {name_r} at the
        group level. Any member of the group inherits this entitlement for as long as their
        membership stands. Individual members are not enumerated here.""",
        f"""Entitlement register update: group-level grant. {name_g} -> {name_r},
        permission {permission}. Inherited by the group's membership. Reviewed annually by the
        data owner.""",
    ])
    return _doc(f"DOC-{n:04d}", "grant", body, [g, r], [
        {"kind": "edge", "relation_type": "GRANTS", "a": g, "b": r, "permission": permission},
    ])


DISTRACTOR_TEMPLATES = [
    ("billing", """Billing query from {a}. The finance contact asked why the invoice arrived
     three days later than usual. Confirmed this was a scheduling change on our side, not a
     change to their agreement. No action required."""),
    ("latency", """Performance complaint from {a}. Users reported that dashboards were slow to
     load between 09:00 and 11:00 on Tuesday. Traced to a regional cache warm-up. Mitigated the
     same day and the customer confirmed recovery."""),
    ("password", """Password reset for a user at {a}. Identity verified over the phone against
     the account security questions. Reset link issued and confirmed used. No entitlement change
     was made as part of this ticket."""),
    ("feature", """Feature request logged by {a}. They would like scheduled exports in their own
     timezone rather than UTC. Passed to product for triage. No commitment given on timing."""),
    ("onboarding", """Onboarding check-in with {a}. Walked through the getting-started material
     and answered questions about the admin console. The team is comfortable proceeding. Follow
     up in two weeks."""),
    ("renewal", """Renewal conversation with {a}. The account is tracking to renew on the usual
     terms. Procurement asked for a copy of the security whitepaper, which has been sent."""),
    ("outage", """Incident note. {a} was affected by the partial outage on the shared ingestion
     tier. Service was restored within the hour and a summary was shared with their team."""),
    ("training", """Training session delivered to {a}. Covered report building and sharing
     etiquette. Attendance was good and the recording has been made available to them."""),
]


def distractor_doc(n, vid, name, rng):
    """Mentions an entity but asserts no graph fact. Retrieval noise, by design."""
    kind, tpl = rng.choice(DISTRACTOR_TEMPLATES)
    return _doc(f"DOC-{n:04d}", f"distractor_{kind}", tpl.format(a=name), [vid], [])


def near_miss_doc(n, c, r, name_c, name_r, rng):
    """Reads like an access grant, but the request was DENIED -- correct
    answer excludes it."""
    body = rng.choice([
        f"""Access request -- DENIED. {name_c} asked whether they could be given access to
        {name_r}. The data owner reviewed the justification and declined; no entitlement was
        granted. The requester was pointed at an aggregated alternative instead. Ticket closed
        as rejected.""",
        f"""Escalation summary. {name_c} chased a previous request covering {name_r}. Confirmed
        with governance that the request remains refused and nothing has been provisioned. The
        account holds no entitlement to this asset.""",
        f"""Note for the record: a request from {name_c} concerning {name_r} was withdrawn
        before approval. No access was ever granted and none is pending.""",
    ])
    return _doc(f"DOC-{n:04d}", "near_miss", body, [c, r], [])


# --------------------------------------------------------------------------
def generate(out_dir: Path, seed: int = SEED) -> Dict[str, Any]:
    vertices, edges = build_graph(seed)
    rng = random.Random(seed + 1)  # separate stream from the graph rng

    names = build_names(vertices, rng)
    aliases = build_aliases(vertices, names)
    props = {v.vid: v.props for v in vertices}
    label = {v.vid: v.label for v in vertices}

    # Collapse bidirectional edges to logical pairs, one fact each.
    logical: List[Dict[str, Any]] = []
    seen_pairs: set[tuple] = set()
    for e in edges:
        rt = e.props["relation_type"]
        key = (rt, *sorted((e.out_id, e.in_id)))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        a, b = e.out_id, e.in_id
        # Orient: Customer->Resource, Customer->Group, Group->Resource.
        if rt in ("ACCESS", "MEMBER") and label[a] != "Customer":
            a, b = b, a
        if rt == "GRANTS" and label[a] != "Group":
            a, b = b, a
        logical.append({"relation_type": rt, "a": a, "b": b, "props": dict(e.props)})

    docs: List[Dict[str, Any]] = []
    n = 0

    for v in vertices:
        n += 1
        if v.label == "Customer":
            docs.append(account_profile(n, v.vid, v.props, names[v.vid], rng))
        elif v.label == "Resource":
            docs.append(resource_catalog(n, v.vid, v.props, names[v.vid], rng))
        else:
            docs.append(group_charter(n, v.vid, v.props, names[v.vid], rng))

    for lg in logical:
        n += 1
        a, b, p = lg["a"], lg["b"], lg["props"]
        if lg["relation_type"] == "CUSTOMER_LINK":
            docs.append(relationship_doc(n, a, b, names[a], names[b], p, rng))
        elif lg["relation_type"] == "ACCESS":
            docs.append(access_doc(n, a, b, names[a], names[b], p["permission"], rng))
        elif lg["relation_type"] == "MEMBER":
            docs.append(membership_doc(n, a, b, names[a], names[b], rng))
        else:
            docs.append(grant_doc(n, a, b, names[a], names[b], p["permission"], rng))

    # Distractors: entity names, no edges.
    customer_ids = [v.vid for v in vertices if v.label == "Customer"]
    for vid in [customer_ids[i % len(customer_ids)] for i in range(0, 40 * 7, 7)][:40]:
        n += 1
        docs.append(distractor_doc(n, vid, names[vid], rng))

    # Near-misses: pairs that are explicitly not access edges.
    real_access = {(lg["a"], lg["b"]) for lg in logical if lg["relation_type"] == "ACCESS"}
    resource_ids = [v.vid for v in vertices if v.label == "Resource"]
    made = 0
    for i, c in enumerate(customer_ids):
        if made >= 10:
            break
        r = resource_ids[(i * 3 + 1) % len(resource_ids)]
        if (c, r) in real_access:
            continue
        n += 1
        docs.append(near_miss_doc(n, c, r, names[c], names[r], rng))
        made += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    entities = {
        vid: {"id": vid, "label": label[vid], "name": names[vid], "props": props[vid]}
        for vid in props
    }
    (out_dir / "docs.json").write_text(json.dumps(docs, indent=1), encoding="utf-8")
    (out_dir / "entities.json").write_text(json.dumps(entities, indent=1), encoding="utf-8")
    (out_dir / "aliases.json").write_text(json.dumps(aliases, indent=1), encoding="utf-8")
    (out_dir / "gold_edges.json").write_text(json.dumps(logical, indent=1), encoding="utf-8")

    return {
        "documents": len(docs),
        "entities": len(entities),
        "aliases": len(aliases),
        "gold_edges": len(logical),
        "words": sum(len(d["text"].split()) for d in docs),
        "by_type": {t: sum(1 for d in docs if d["doc_type"].startswith(t))
                    for t in ("account_profile", "resource_catalog", "group_charter",
                              "relationship", "access", "membership", "grant",
                              "distractor", "near_miss")},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=None,
                    help="default: <repo>/data/corpus")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else \
        Path(__file__).resolve().parent.parent / "data" / "corpus"
    stats = generate(out_dir, args.seed)
    print(f"Wrote corpus to {out_dir}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
