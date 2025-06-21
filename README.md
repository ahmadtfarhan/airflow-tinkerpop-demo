# airflow-tinkerpop-demo

This project demonstrates how to use Apache Airflow to build a data pipeline that extracts data from MySQL and loads it into a TinkerPop-compatible graph database (Gremlin).

## Prerequisites

- Docker
- Docker Compose

## Setup

### 1. Set Airflow User

On Linux, you should set the `AIRFLOW_UID` environment variable to your user ID to avoid file permission issues with files created in the `dags`, `logs`, and `plugins` directories.

```bash
echo "AIRFLOW_UID=$(id -u)" > .env
```

On other operating systems, you can ignore this step.

### 2. Run the services

Start the services using `docker-compose`:

```bash
docker-compose up --build -d
```

This will start the following services:
- Airflow (scheduler, worker, webserver)
- PostgreSQL (Airflow metadata database)
- Redis (Airflow Celery broker)
- MySQL (data source)
- Gremlin (Tinkerpop graph database)

### 3. Configure and Populate

After starting the services, you need to configure the connections and populate the databases.

1.  **Run the setup script:**
    This script handles setting up Airflow connections and populating the Gremlin graph.
    ```bash
    ./setup-connections.sh
    ```

2.  **Populate the MySQL database:**
    This command loads the sample movie data into the MySQL database.
    ```bash
    docker-compose exec -T mysqldb mysql -vvv -u user -p'password' test_db < sql/movies.sql
    ```

## Running the DAG

1. In the Airflow UI, go to the **DAGs** page.
2. You should see the `mysql_to_gremlin` DAG.
3. Enable the DAG by clicking the toggle button.
4. The DAG is scheduled to run hourly, but you can trigger it manually by clicking the "play" button.

## Stopping the project

To stop all the services, run:

```bash
docker-compose down
```

This will stop and remove the containers. The data in PostgreSQL, MySQL and Gremlin will be persisted in Docker volumes.
If you want to remove the volumes as well, run:
```bash
docker-compose down -v
```