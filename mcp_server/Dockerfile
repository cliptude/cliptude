FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY mcp_server /app
RUN pip install --no-cache-dir . \
    && groupadd --system --gid 10001 cliptude \
    && useradd --system --uid 10001 --gid cliptude --home-dir /home/cliptude --create-home \
        --shell /usr/sbin/nologin cliptude

ENV CLIPTUDE_MCP_TRANSPORT=streamable-http
ENV CLIPTUDE_MCP_HOST=0.0.0.0
ENV CLIPTUDE_MCP_PORT=8787
ENV HOME=/home/cliptude
EXPOSE 8787

# The hosted server never runs as root.
USER 10001:10001

CMD ["cliptude-mcp"]
