FROM python:3.12-slim

RUN apt-get update && apt-get install -y git curl bash && rm -rf /var/lib/apt/lists/*

RUN pip install anthropic anyio

WORKDIR /workspace

COPY agents/ /agents/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
