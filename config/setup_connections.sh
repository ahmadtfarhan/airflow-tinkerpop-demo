#!/bin/bash
# Wait for the Airflow database to be ready
# The 'airflow db check' command waits for the database to be available.
# It's useful in containerized setups to ensure the database service is up before other services try to connect.
airflow db check --retry 30 --retry-delay 5

# Add Airflow connections
# These commands add the necessary connections for the DAGs to run.
airflow connections add 'gremlin' --conn-uri 'ws://mylogin:mysecret@host.docker.internal:8182/gremlin'
airflow connections add 'mysql_default' --conn-uri 'mysql://root:password@host.docker.internal:3306/test_db'