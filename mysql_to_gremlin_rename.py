# dags/mysql_to_gremlin.py

from datetime import datetime
import logging
from airflow.sdk import asset, dag, task

from airflow.operators.python import PythonOperator
from airflow.providers.mysql.hooks.mysql import MySqlHook
from airflow.hooks.base import BaseHook
from airflow.sdk import Asset
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.structure.graph import Graph

# Re-use the same Asset URI
mysql_table_asset = Asset(
    uri="mysql://host.docker.internal/test_db/testtable",
    name="my_table_asset",
)

def transfer_new_row(**context):
    # 1) Read the newest record from our Docker-hosted MySQL
    mysql = MySqlHook(mysql_conn_id="mysql_default")
    df = mysql.get_pandas_df(
        "SELECT * FROM testtable ORDER BY id DESC LIMIT 1"
    )
    record = df.to_dict(orient="records")[0]
    logging.info(f"New record to transfer: {record}")
    # 2) Load Gremlin Server endpoint from Airflow Connection "gremlin_server"
    # gremlin_conn = BaseHook.get_connection("gremlin_server")
    # #    we expect either:
    # #      * host & port set (ws://host:port/gremlin)
    # #      * or an extra_dejson["endpoint"] field
    # endpoint = (
    #     gremlin_conn.extra_dejson.get("endpoint")
    #     or f"ws://{gremlin_conn.host}:{gremlin_conn.port}/gremlin"
    # )

    # # 3) Write to Gremlin
    # remote_conn = DriverRemoteConnection(endpoint, 'g')
    # g = Graph().traversal().withRemote(remote_conn)

    # v = g.addV('my_record').property('id', record['id'])
    # for col, val in record.items():
    #     if col != 'id':
    #         v = v.property(col, val)
    # v.next()
    # remote_conn.close()

with DAG(
    dag_id="mysql_to_gremlin",
    start_date=datetime(2025, 1, 1),
    schedule=[mysql_table_asset],   # asset-driven trigger
    catchup=False,
    tags=["asset-driven"],
) as dag:

    send_to_gremlin = PythonOperator(
        task_id="transfer_to_gremlin",
        python_callable=transfer_new_row,
        inlets=[mysql_table_asset],
    )
