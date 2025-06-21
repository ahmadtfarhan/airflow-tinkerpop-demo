FROM apache/airflow:3.0.2
RUN pip install apache-airflow==3.0.2
RUN pip install apache-airflow-providers-apache-tinkerpop
RUN pip install apache-airflow-providers-mysql
