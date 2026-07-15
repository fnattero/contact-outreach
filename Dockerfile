FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app --home /app app

COPY pyproject.toml README.md ./
RUN mkdir -p src \
    && pip install --no-cache-dir ".[dev]" \
    && rm -rf /app/build /app/src

COPY --chown=app:app . .
RUN mkdir -p /app/private \
    && chown -R app:app /app \
    && chmod +x /app/scripts/entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["web"]
