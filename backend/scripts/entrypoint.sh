#!/bin/sh
set -eu

role="${1:-backend}"

case "$role" in
    backend)
        exec /app/scripts/start-backend.sh
        ;;
    migrate)
        exec python src/manage.py migrate_safe
        ;;
    bootstrap)
        exec python src/manage.py bootstrap_owner
        ;;
    check)
        exec python src/manage.py check
        ;;
    *)
        exec "$@"
        ;;
esac
