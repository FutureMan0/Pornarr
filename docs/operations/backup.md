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

## Database

```
docker compose exec -T postgres pg_dump -U pornarr -Fc pornarr > pornarr-$(date +%F).dump
```

Custom format, because it restores selectively and compresses. Keep at least seven
daily and four weekly copies, and keep at least one off the machine that runs
Pornarr.

## Restore

```
docker compose up -d postgres redis
docker compose exec -T postgres pg_restore -U pornarr -d pornarr --clean --if-exists < pornarr.dump
docker compose run --rm migrate
docker compose up -d
```

Migrations run after the restore, not before: the dump may predate the current schema.

## Verification

A backup that has never been restored is a hypothesis. Restore into a scratch database
periodically and confirm that the media count, the user count and one integration
credential all survive. The credential is the part people discover too late.
