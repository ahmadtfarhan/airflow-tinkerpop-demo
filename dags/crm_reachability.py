"""Account reachability traversal: walk linked accounts, then collect every
resource reachable directly or through group inheritance. Gold answer source
for the B3 questions.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.apache.tinkerpop.operators.gremlin import GremlinOperator
from airflow.sdk import Param, dag

from include.paths import GREMLIN_CONN

# No leading `__.`: chaining two `__.`-prefixed fragments breaks Gremlin
# ("No such property: __"). Callers needing an anonymous traversal prefix it themselves.
NETWORK = (
    "union(__.identity(), "
    "__.repeat(__.bothE('RELATED_TO').has('relation_type','CUSTOMER_LINK')"
    ".otherV().simplePath()).emit().times(6)).dedup()"
)

# Resources reachable from a set of accounts: direct access, or via group membership.
RESOURCES_OF = (
    "union("
    "__.bothE('RELATED_TO').has('relation_type','ACCESS').otherV().hasLabel('Resource'), "
    "__.bothE('RELATED_TO').has('relation_type','MEMBER').otherV().hasLabel('Group')"
    ".bothE('RELATED_TO').has('relation_type','GRANTS').otherV().hasLabel('Resource'))"
)

REACHABILITY = (
    "g.V().hasLabel('Customer')"
    "{filter}"
    ".project('account','resources')"
    ".by(values('id'))"
    f".by(__.{NETWORK}.{RESOURCES_OF}.values('id').dedup().fold())"
)

DOC_MD = """
### Account reachability

> If this account were compromised, what would be exposed?

Walks `CUSTOMER_LINK` edges to any depth, then collects resources reachable
directly (`ACCESS`) or inherited through a group (`MEMBER` -> `GRANTS`).

Leave `account_id` empty to run it across every account.
"""


@dag(
    dag_id="crm_reachability",
    schedule=None,
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["summit26", "graph"],
    doc_md=DOC_MD,
    params={
        "account_id": Param(
            "C059", type="string",
            description="Account to start from. Empty string runs every account.",
        )
    },
    render_template_as_native_obj=False,
)
def crm_reachability():
    GremlinOperator(
        task_id="reachable_resources",
        gremlin_conn_id=GREMLIN_CONN,
        # `query` is a template_field, so params render here.
        query=REACHABILITY.format(
            filter="{% if params.account_id %}"
                   ".has('id','{{ params.account_id }}')"
                   "{% endif %}"
        ),
    )


crm_reachability()
