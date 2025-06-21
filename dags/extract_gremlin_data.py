# dags/extract_gremlin_data.py
import logging
from airflow.sdk import asset, Asset, dag, task

from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook


@dag(
    dag_id='extract_gremlin_data',
    schedule=[Asset("batch_extraction")],
)
def gremlin_data_extraction_dag():
    @task
    def extract_gremlin_data():
        gremlin_hook = GremlinHook(conn_id='gremlin')
        gremlin_query = "g.V().project('id', 'label', 'properties').by(id()).by(label()).by(valueMap())"
        data = gremlin_hook.run(gremlin_query)
        logging.info(f"Successfully extracted {len(data)} records.")
        return data

    extract_gremlin_data()

gremlin_data_extraction_dag()