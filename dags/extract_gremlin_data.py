# dags/mysql_to_gremlin.py

import logging
from airflow.sdk import asset, Asset

from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook

@asset(schedule=[Asset("batch_extraction")])
def extract_gremlin_data():
    gremlin_hook = GremlinHook(conn_id='gremlin')
    gremlin_query = "g.V().project('id', 'label', 'properties').by(id()).by(label()).by(valueMap())"
    data = gremlin_hook.run(gremlin_query)
    logging.info(f'Extracted data: {data}')
