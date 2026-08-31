# Latest Airflow release. The auth-manager surface I was wary about turned out
# not to have moved -- 3.3.1 still uses FabAuthManager -- but 3.2 did introduce
# required JWT settings for the execution API, which docker-compose.yaml sets.
#
# On 3.3+, AgentOperator(durable=True) caches into the AIP-103 task state store
# with no extra configuration. Rolling back to 3.1.x means restoring
# AIRFLOW__COMMON_AI__DURABLE_CACHE_PATH as a real setting rather than a no-op.
#
# postgres:13 is deliberately NOT bumped alongside this. Airflow 3.3 still
# supports it, and an in-place major-version swap will not start: pg13 data
# directories are unreadable by pg16, and recovering means `down -v`, which
# wipes the metadata DB holding the pre-baked demo run.
FROM apache/airflow:3.3.1-python3.12

# NOTE: no `pip install apache-airflow==...` here. Reinstalling core on top of
# the official image is how you get a subtly broken image.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
