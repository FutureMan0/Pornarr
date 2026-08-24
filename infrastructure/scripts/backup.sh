#!/usr/bin/env bash

# Write a PostgreSQL custom-format dump and a secret-free copy of bootstrap
# configuration. Restore the original APP_SECRET separately; it decrypts every
# integration credential stored in the database.
set -euo pipefail

backup_dir="${BACKUP_DIR:-./backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_path="${backup_dir}/pornarr-${timestamp}.dump"
config_path="${backup_dir}/pornarr-${timestamp}.env"

if [[ ! -f .env ]]; then
  echo ".env is required to export reproducible non-secret configuration" >&2
  exit 1
fi

umask 077
mkdir -p "$backup_dir"
dump_temporary="$(mktemp "${backup_dir}/.pornarr-dump.XXXXXX")"
config_temporary="$(mktemp "${backup_dir}/.pornarr-config.XXXXXX")"
cleanup() {
  rm -f "$dump_temporary" "$config_temporary"
}
trap cleanup EXIT

docker compose exec -T postgres pg_dump -U pornarr -Fc pornarr > "$dump_temporary"

# Connection URLs can hold passwords. All explicitly secret-shaped names stay
# out too, so the result can be stored beside a dump without exposing keys.
sed -E \
  '/^[[:space:]]*(APP_SECRET|DATABASE_URL|REDIS_URL|SECRET|[A-Za-z_][A-Za-z0-9_]*(_SECRET|_PASSWORD|_TOKEN|_KEY))=/d' \
  .env > "$config_temporary"

mv "$dump_temporary" "$dump_path"
mv "$config_temporary" "$config_path"
trap - EXIT

printf 'Wrote %s and %s\n' "$dump_path" "$config_path"
