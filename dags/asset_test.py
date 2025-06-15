# dags/mysql_to_gremlin.py

from datetime import datetime
import logging
from airflow.sdk import asset, dag, task

from airflow.providers.mysql.hooks.mysql import MySqlHook
from airflow.hooks.base import BaseHook
from airflow.sdk import Asset

from airflow.providers.mysql.hooks.mysql import MySqlHook
from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook

# Re-use the same Asset URI
mysql_table_asset = Asset(
    uri="mysql://host.docker.internal/test_db/testtable",
    name="my_table_asset",
)

@dag(
    schedule_interval=None
)
def asset_test():
    