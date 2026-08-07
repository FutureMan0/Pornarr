# Import pipeline

Triggered when a download completes, and by the library scanner for files that are
already on disk. Idempotent: running it twice on the same file changes nothing.

## Steps

**1. Intake.** Extension, size and archive state are checked. Incomplete and unknown
files are rejected before anything expensive happens.

**2. Fingerprint.** oshash over the first and last 64 KB plus file size. Milliseconds,
and it doubles as the StashDB lookup key.

**3. Duplicate check.** An exact oshash match is a certain duplicate. Otherwise a
fuzzy comparison — title similarity above 0.85, same studio, release dates within two
days, durations within five percent — produces an upgrade candidate.

**4. Technical metadata.** ffprobe for resolution, codecs, duration, bitrate, streams
and container.

**5. Scene metadata.** The provider cascade, producing a confidence score.

**6. Normalization.** Release group, resolution tokens, codec names, separators and
date variants are stripped to a canonical title.

**7. Quality verdict.** The release is scored against the quality profile. Not in the
profile, not an upgrade, or already past cutoff all end the import here with a reason.

**8. Filter evaluation.** Global and user filter rules, resolved with the user profile
only ever tightening the global one. The outcome is allow, quarantine or reject, and
every firing rule is written to the audit log.

**9. Placement.** A hardlink into
`/data/library/{studio}/{year}/{normalized_title}/{quality}/`. If the link fails
because the paths are on different filesystems, the file is copied and a warning is
raised. Usenet files may be moved instead, since nothing is seeding.

**10. Artwork.** Poster, preview frames and a hover sprite with its VTT index.

**11. Publication.** The media record becomes available and the event is emitted.

## Afterwards

Perceptual hashing runs as a separate job: sixteen evenly spaced frames, DCT hash, and
a Hamming distance of eight or less records a duplicate candidate. It never deletes
anything; it raises an administrator notice.

## Upgrades

An upgrade imports the new file and activates it, then deletes the previous file only
after the new one verifies. `media.id` never changes, so tags, favourites, events and
recommendation history survive.
