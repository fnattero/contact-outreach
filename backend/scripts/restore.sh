#!/bin/sh
set -eu

if [ "$#" -ne 2 ] || [ "$1" != "--confirm" ]; then
    echo "Uso: scripts/restore.sh --confirm backups/AAAA..." >&2
    echo "La restauración reemplaza la base y los objetos privados actuales." >&2
    exit 2
fi

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"

# COMPOSE overrides the command (the Makefile passes its own; Hetzner adds a second --file).
compose() {
    if [ -n "${COMPOSE:-}" ]; then
        # shellcheck disable=SC2086
        $COMPOSE "$@"
    else
        docker compose --project-directory "$repo_root" --file "$repo_root/infra/docker-compose.yml" "$@"
    fi
}

backup_dir="$(cd "$2" && pwd)"
test -f "$backup_dir/database.dump"
test -f "$backup_dir/objects.tar"
test -f "$backup_dir/checksums.sha256"

(
    cd "$backup_dir"
    sha256sum -c checksums.sha256
)

compose run --rm --no-deps -T backend python src/manage.py shell -c 'from django.conf import settings; raise SystemExit(0 if settings.SEND_KILL_SWITCH else "Activá SEND_KILL_SWITCH=true antes del restore")'
compose stop backend
compose up --detach --wait postgres redis minio
compose run --rm --no-deps minio-init
compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction' < "$backup_dir/database.dump"

compose run --rm --no-deps -T backend python src/manage.py private_objects import < "$backup_dir/objects.tar"
compose run --rm --no-deps -T backend python src/manage.py migrate_safe
compose run --rm --no-deps -T backend python src/manage.py verify_restore --require-kill-switch
compose up --detach --wait

echo "Restore verificado. Revisá el checklist live antes de desactivar el kill switch."
