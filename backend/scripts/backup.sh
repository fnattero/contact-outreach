#!/bin/sh
set -eu

umask 077
destination_root="${1:-backups}"
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"

# COMPOSE overrides the command (the Makefile passes its own; Hetzner adds a second --file).
compose() {
    if [ -n "${COMPOSE:-}" ]; then
        # shellcheck disable=SC2086
        $COMPOSE "$@"
    else
        docker compose --project-directory "$repo_root" --file "$repo_root/infra/docker-compose.yml" --profile storage-s3 "$@"
    fi
}

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_dir="$destination_root/$timestamp"
tmp_dir="$destination_root/.${timestamp}.tmp"

mkdir -p "$destination_root"
if [ -e "$final_dir" ] || [ -e "$tmp_dir" ]; then
    echo "El destino de backup ya existe: $final_dir" >&2
    exit 1
fi
mkdir -p "$tmp_dir"
cleanup() {
    rm -rf "$tmp_dir"
}
trap cleanup EXIT HUP INT TERM

compose exec -T backend python src/manage.py verify_restore
# The database goes first: an object stored after the dump is an orphan the restore ignores, while
# the reverse order could leave a row pointing at an object the backup does not contain.
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$tmp_dir/database.dump"
test -s "$tmp_dir/database.dump"
compose exec -T backend python src/manage.py private_objects export > "$tmp_dir/objects.tar"
tar -xOf "$tmp_dir/objects.tar" manifest.tsv > "$tmp_dir/objects.manifest.tsv"

commit="$(git -C "$repo_root" rev-parse --verify HEAD 2>/dev/null || printf unknown)"
{
    printf 'created_at_utc=%s\n' "$timestamp"
    printf 'git_commit=%s\n' "$commit"
    printf 'contains=postgresql,private_objects\n'
    printf 'object_count=%s\n' "$(wc -l < "$tmp_dir/objects.manifest.tsv" | tr -d ' ')"
    printf 'encryption_key_included=false\n'
} > "$tmp_dir/manifest.txt"

(
    cd "$tmp_dir"
    sha256sum database.dump objects.tar objects.manifest.tsv > checksums.sha256
)
mv "$tmp_dir" "$final_dir"
trap - EXIT HUP INT TERM
echo "Backup consistente creado en $final_dir"
echo "FIELD_ENCRYPTION_KEY no está incluido: respaldala por un canal separado."
