FROM python:3.13-slim

# Install Cloud SQL Proxy
ADD https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.3/cloud-sql-proxy.linux.amd64 /usr/local/bin/cloud-sql-proxy
RUN chmod +x /usr/local/bin/cloud-sql-proxy

# Install Python deps
RUN pip install --no-cache-dir \
    httpx \
    pynacl \
    psycopg2-binary \
    web3 \
    eth-account \
    google-cloud-secret-manager

WORKDIR /demos
COPY . .

RUN chmod +x run_integration.sh

ENTRYPOINT ["./run_integration.sh"]
