#Built-in imports
import logging
from datetime import datetime


#Airflow imports
from airflow import DAG
from airflow.decorators import dag, task

#Gremlin imports
from airflow.providers.apache.tinkerpop.hooks.gremlin import GremlinHook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


args = {
    "owner": "Airflow",
    "start_date": datetime(2023, 5, 21, 14, 00)
}


@dag(
    dag_id='gremlin_data',
    default_args=args, 
    catchup=False,
    max_active_runs=1
)
def run_cosmos():

    @task
    def query_gremlin_data():  
        try:

            hook = GremlinHook('gremlin')
            query = "g.V().project('id', 'label', 'properties').by(id()).by(label()).by(valueMap())"
            
            res = hook.run(query=query)
            logging.info(f"Number of vertices returned: {len(res)}")
            for vertex in res:
                logging.info(f"vertex: {vertex}")

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            raise

    query_gremlin_data()

run_cosmos = run_cosmos()
