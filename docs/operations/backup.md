# Backup and restore

Three things need backing up, and they are not equally important.

**The database.** Everything Pornarr knows that is not derivable from the files:
users, requests, history, quality profiles, filters, monitors, interest profiles.
Losing it means losing the product, even if every video survives.

**The secret key.** `APP_SECRET` derives the encryption key for every stored
credential. A database restored without the matching secret has unreadable indexer
keys, client passwords and OIDC secrets, and every integration must be reconfigured
by hand.

**The library.** Large, and usually already covered by whatever protects the storage
itself.

Thumbnails and transcode segments are regenerable and are not worth backing up.

## Create a backup

```
make backup
```

This writes two timestamped files to `./backups` (or `BACKUP_DIR` when supplied):

- `pornarr-<timestamp>.dump` is a compressed PostgreSQL custom-format dump.
- `pornarr-<timestamp>.env` is the non-secret bootstrap configuration needed to
  reproduce the instance.

The config export excludes `APP_SECRET`, database and Redis URLs, and values with
secret-shaped names such as `*_TOKEN`, `*_PASSWORD`, and `*_KEY`. Store the original
`APP_SECRET` in a separate secret manager. It is required to decrypt every restored
integration credential.

Keep at least seven daily and four weekly copies, and keep at least one off the
machine that runs Pornarr. Media is deliberately not included; protect that storage
with its own backup system.

## Restore

```
# Recreate the non-secret configuration and restore the original APP_SECRET from
# the secret manager. Add custom database and Redis URLs if the defaults do not apply.
cp .env.example .env
cat /secure/backups/pornarr-<timestamp>.env >> .env
$EDITOR .env

# Stop all writers without deleting persistent database data.
docker compose down
docker compose up -d postgres redis
docker compose exec -T postgres pg_restore -U pornarr -d pornarr --clean --if-exists \
  < /secure/backups/pornarr-<timestamp>.dump
docker compose run --rm migrate
docker compose up -d
curl --fail http://localhost:8000/health
```

Migrations run after the restore, not before: the dump may predate the current schema.
If `APP_SECRET` does not match the database, the API refuses to start with a clear
error rather than leaving integrations to fail later.

Set `BACKUP_MAX_AGE_HOURS` to make `/api/health` report the age of the newest `.dump`
in `BACKUP_PATH`. In Compose, set `BACKUP_PATH_HOST` to the host directory containing
the dumps (the default is `./backups`, mounted at `/backups`). A missing or stale
backup degrades health but does not stop the running instance.

## Verification

A backup that has never been restored is a hypothesis. Restore into a scratch database
periodically and confirm that the media count, the user count and one integration
credential all survive. The credential is the part people discover too late.
