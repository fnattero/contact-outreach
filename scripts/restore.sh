#!/bin/sh
set -eu

if [ "$#" -ne 2 ] || [ "$1" != "--confirm" ]; then
    echo "Uso: scripts/restore.sh --confirm backups/AAAA..." >&2
    echo "La restauración reemplaza la base y el volumen privado actuales." >&2
    exit 2
fi

backup_dir="$(cd "$2" && pwd)"
test -f "$backup_dir/database.dump"
test -f "$backup_dir/checksums.sha256"
test -d "$backup_dir/catalogs"

(
    cd "$backup_dir"
    sha256sum -c checksums.sha256
)

docker compose run --rm --no-deps web python src/manage.py shell -c 'from django.conf import settings; raise SystemExit(0 if settings.SEND_KILL_SWITCH else "Activá SEND_KILL_SWITCH=true antes del restore")'
docker compose stop worker beat web
docker compose up --detach postgres redis
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < "$backup_dir/database.dump"

docker compose create web
docker compose run --rm --no-deps --user root web sh -c 'find /app/private -mindepth 1 -delete'
docker compose cp "$backup_dir/catalogs/." web:/app/private/
docker compose run --rm --no-deps --user root web chown -R app:app /app/private
docker compose run --rm web python src/manage.py migrate_safe
docker compose run --rm web python src/manage.py verify_restore --require-kill-switch
docker compose up --detach --wait web worker beat

echo "Restore verificado. Revisá el checklist live antes de desactivar el kill switch."
