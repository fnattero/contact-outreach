#!/bin/sh
set -eu

case "${APP_ENV:-development}" in
    production)
        python src/manage.py check --deploy
        ;;
    *)
        python src/manage.py check
        ;;
esac

python src/manage.py wait_dependencies --timeout "${DEPENDENCY_WAIT_SECONDS:-60}"

run_migrations="${RUN_MIGRATIONS_ON_STARTUP:-true}"
case "$run_migrations" in
    true)
        runtime_database_url="${DATABASE_URL:-}"
        if [ -n "${MIGRATION_DATABASE_URL:-}" ]; then
            DATABASE_URL="$MIGRATION_DATABASE_URL"
            export DATABASE_URL
        fi
        python src/manage.py migrate_safe
        DATABASE_URL="$runtime_database_url"
        export DATABASE_URL
        unset MIGRATION_DATABASE_URL
        ;;
    false)
        unset MIGRATION_DATABASE_URL
        ;;
    *)
        echo "RUN_MIGRATIONS_ON_STARTUP must be true or false" >&2
        exit 1
        ;;
esac

case "${RUN_OWNER_BOOTSTRAP_ON_STARTUP:-false}" in
    true)
        python src/manage.py bootstrap_owner --if-configured
        ;;
    false)
        ;;
    *)
        echo "RUN_OWNER_BOOTSTRAP_ON_STARTUP must be true or false" >&2
        exit 1
        ;;
esac
unset OWNER_USERNAME OWNER_EMAIL OWNER_PASSWORD

mkdir -p /tmp/contact-outreach
exec supervisord --nodaemon --configuration /app/supervisord.conf
