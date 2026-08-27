#!/bin/sh
set -eu

umask 077
destination_root="${1:-backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_dir="$destination_root/$timestamp"
tmp_dir="$destination_root/.${timestamp}.tmp"

mkdir -p "$destination_root"
if [ -e "$final_dir" ] || [ -e "$tmp_dir" ]; then
    echo "El destino de backup ya existe: $final_dir" >&2
    exit 1
fi
mkdir -p "$tmp_dir/catalogs"
cleanup() {
    rm -rf "$tmp_dir"
}
trap cleanup EXIT HUP INT TERM

docker compose exec -T web python src/manage.py verify_restore
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$tmp_dir/database.dump"
docker compose cp web:/app/private/. "$tmp_dir/catalogs"

commit="$(git rev-parse --verify HEAD 2>/dev/null || printf unknown)"
{
    printf 'created_at_utc=%s\n' "$timestamp"
    printf 'git_commit=%s\n' "$commit"
    printf 'contains=postgresql,private_catalogs\n'
    printf 'encryption_key_included=false\n'
} > "$tmp_dir/manifest.txt"

(
    cd "$tmp_dir"
    find database.dump catalogs -type f -print0 | sort -z | xargs -0 sha256sum > checksums.sha256
)
mv "$tmp_dir" "$final_dir"
trap - EXIT HUP INT TERM
echo "Backup consistente creado en $final_dir"
echo "FIELD_ENCRYPTION_KEY no está incluido: respaldala por un canal separado."
