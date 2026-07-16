#!/bin/sh
set -eu

role="${1:-web}"

case "$role" in
    web)
        python src/manage.py migrate_safe
        python src/manage.py bootstrap_owner --if-configured
        exec gunicorn contact_outreach.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers "${WEB_CONCURRENCY:-2}" \
            --timeout "${WEB_TIMEOUT:-60}" \
            --access-logfile /dev/null \
            --error-logfile -
        ;;
    worker)
        exec celery -A contact_outreach worker \
            --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
            --hostname="worker@%h" \
            --concurrency="${CELERY_WORKER_CONCURRENCY:-2}"
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
