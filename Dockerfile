FROM python:3.12-slim

ARG BUILD_COMMIT=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MULTITRADE_BUILD_COMMIT=${BUILD_COMMIT}

LABEL org.opencontainers.image.revision="${BUILD_COMMIT}"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 trader \
    && mkdir -p /app/var \
    && chown -R trader:trader /app/var

USER trader

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD ["multitrade", "healthcheck"]

CMD ["multitrade", "run"]
