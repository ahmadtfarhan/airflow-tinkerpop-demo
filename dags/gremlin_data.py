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
def run_gremlin_data():

    @task
    def query_gremlin_data():  
        try:

            hook = GremlinHook('gremlin')
            # query = "g.V().project('id', 'label', 'properties').by(id()).by(label()).by(valueMap())"
            query = """
                g.V().hasLabel('Customer').as('start')
                .project('customer','resources')
                .by(values('id'))
                .by(
                    __.union(
                        __.identity(),
                        __.repeat(
                            __.bothE('RELATED_TO').has('relation_type','CUSTOMER_LINK')
                            .otherV()
                            .simplePath()
                        ).emit().times(6)
                    )
                    .dedup()
                    .bothE('RELATED_TO').has('relation_type','ACCESS')
                    .otherV().hasLabel('Resource')
                    .values('id')
                    .dedup()
                    .fold()
                )
            """
            
            res = hook.run(query=query)
            logging.info(res)
            for vertex in res:
                logging.info(f"custoemr resources: {vertex}")

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            raise

    query_gremlin_data()

run_gremlin_data = run_gremlin_data()
