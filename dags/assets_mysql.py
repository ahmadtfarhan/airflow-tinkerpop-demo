# dags/assets_mysql.py

from airflow.sdk import Asset

# *Changed* uri to point at our local-docker connection "mysql_local"
mysql_table_asset = Asset(
    uri="mysql://host.docker.internal/test_db/testtable",
    name="my_table_asset",
)
