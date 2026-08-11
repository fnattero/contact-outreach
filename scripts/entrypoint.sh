#!/bin/sh
set -eu

role="${1:-web}"

case "$role" in
    web)
        run_migrations="${RUN_MIGRATIONS_ON_STARTUP:-}"
        if [ -z "$run_migrations" ]; then
            case "${APP_ENV:-development}" in
                production) run_migrations=false ;;
                *) run_migrations=true ;;
            esac
        fi
        case "$run_migrations" in
            true) python src/manage.py migrate_safe ;;
            false) : ;;
            *) echo "RUN_MIGRATIONS_ON_STARTUP must be true or false" >&2; exit 1 ;;
        esac

        run_owner_bootstrap="${RUN_OWNER_BOOTSTRAP_ON_STARTUP:-}"
        if [ -z "$run_owner_bootstrap" ]; then
            case "${APP_ENV:-development}" in
                production) run_owner_bootstrap=false ;;
                *) run_owner_bootstrap=true ;;
            esac
        fi
        case "$run_owner_bootstrap" in
            true) python src/manage.py bootstrap_owner --if-configured ;;
            false) : ;;
            *) echo "RUN_OWNER_BOOTSTRAP_ON_STARTUP must be true or false" >&2; exit 1 ;;
        esac

        python src/manage.py collectstatic --noinput
        bind_host="${WEB_BIND_HOST:-0.0.0.0}"
        bind_port="${PORT:-8000}"
        case "$bind_port" in
            ''|*[!0-9]*) echo "PORT must be a numeric TCP port" >&2; exit 1 ;;
        esac
        if [ "$bind_port" -lt 1 ] || [ "$bind_port" -gt 65535 ]; then
            echo "PORT must be between 1 and 65535" >&2
            exit 1
        fi
        case "$bind_host" in
            *:*) bind="[$bind_host]:$bind_port" ;;
            *) bind="$bind_host:$bind_port" ;;
        esac
        exec gunicorn contact_outreach.wsgi:application \
            --bind "$bind" \
            --workers "${WEB_CONCURRENCY:-2}" \
            --timeout "${WEB_TIMEOUT:-60}" \
            --access-logfile /dev/null \
            --error-logfile -
        ;;
    migrate)
        exec python src/manage.py migrate_safe
        ;;
    bootstrap)
        exec python src/manage.py bootstrap_owner
        ;;
    worker)
        exec celery -A contact_outreach worker \
            --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
            --hostname="worker@%h" \
            --concurrency="${CELERY_WORKER_CONCURRENCY:-2}"
        ;;
    maintenance-worker)
        exec celery -A contact_outreach worker \
            --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
            --hostname="maintenance@%h" \
            --queues=maintenance \
            --concurrency=1 \
            --prefetch-multiplier=1 \
            --max-tasks-per-child=1
        ;;
    beat)
        exec celery -A contact_outreach beat \
            --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
            --pidfile=/tmp/celerybeat.pid \
            --schedule=/tmp/celerybeat-schedule
        ;;
    *)
        exec "$@"
        ;;
esac
