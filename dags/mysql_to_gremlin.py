from datetime import datetime
import logging
from airflow.sdk import dag, task
from airflow.sdk import Asset

from airflow.providers.mysql.hooks.mysql import MySqlHook
from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook

args = {
    "owner": "Airflow",
    "start_date": datetime(2023, 5, 21, 14, 00)
}

@dag(
    dag_id='mysql_to_gremlin',
    default_args=args, 
    catchup=False,
    max_active_runs=1,
    schedule="@hourly"
)
def mysql_to_gremlin():
    @task
    def extract_mysql_data():
        mysql_hook = MySqlHook(mysql_conn_id='mysql_default')
        sql = "SELECT id, type, name, year FROM movies"
        return mysql_hook.get_pandas_df(sql)

    @task
    def transform_data(df):
        df['id'] = df['id'].astype(str)
        df['type'] = df['type'].astype(str)
        df['name'] = df['name'].astype(str)
        df['year'] = df['year'].astype(int)
        return df
    
    @task(outlets=[Asset("batch_extraction")])
    def load_to_gremlin(df):
        gremlin_hook = GremlinHook(conn_id='gremlin')
        for _, row in df.iterrows():
            gremlin_query = (
                f"g.addV('{row['type']}')"
                f".property('id', '{row['id']}')"
                f".property('name', '{row['name']}')"
                f".property('year', '{row['year']}')"
            )
            gremlin_hook.run(gremlin_query)
        logging.info("Data loaded to Gremlin successfully.")
        
    extract_mysql_data = extract_mysql_data()
    transformed_data = transform_data(extract_mysql_data)
    load_to_gremlin(transformed_data)

mysql_to_gremlin_dag = mysql_to_gremlin()