"""Fill an empty database with enough to look at.

WHY THIS EXISTS. Every screen in this application is a view of data, so an empty
server shows a dozen honest empty states and nothing else. Judging a design
against that is impossible: the library grid, the facet counts, the rating
distribution and the related row all only exist once there is something to
count.

WHAT IT IS NOT. Not a fixture for tests — those build exactly what they assert
against, and sharing a fixture between "what the suite proves" and "what a
person looks at" makes both worse. Not a migration either: nothing here is
schema, and none of it should ever reach a real library.

IT REFUSES A SERVER THAT IS IN USE. Running this against a household's actual
database would add invented titles to their library, and there is no undo. The
guard is the presence of media, not of the tables — a server mid-setup has
tables and no titles, which is exactly when seeding is wanted.

THE FILES ARE REAL, THE CONTENT IS NOT. Each title gets a small file on disk so
that scanning, hashing and the storage figures have something to work with; they
are zero-filled, so nothing plays. That is deliberate — a demo that ships media
is a demo nobody can check into a repository.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_api.auth import hash_password
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.entities import (
    MediaPerformer,
    MediaTag,
    Performer,
    Studio,
    Tag,
)
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import PlaybackProgress
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.social import (
    Collection,
    CollectionItem,
    CollectionVisibility,
    Comment,
    CommentReport,
    Rating,
    Short,
    ShortSource,
)
from pornarr_db.models.user import User, UserRole
from pornarr_db.models.watchlist import WatchlistEntry

# Fixed, so two runs produce the same library and a screenshot stays comparable.
random.seed(20260817)

PASSWORD = "correct horse battery staple"

ADMIN = "kai"
GUESTS = ("jonas", "mira", "tobi", "lea")

STUDIOS = ("Aurora Studios", "Meridian", "Nightfall", "Independent")

PERFORMERS = ("Ada Vance", "Bex Oyelaran", "Cass Iversen", "Dev Raji", "Elin Sorby")

TAGS = (
    "low-light",
    "interior",
    "handheld",
    "16mm",
    "two-person",
    "hdr",
    "upscaled",
    "monochrome",
    "coastal",
    "night",
)

# Titles rather than filenames: the scanner derives a title from a path, and a
# demo of the design should show the shape of a matched library, not an unmatched
# one.
TITLES = (
    "Aurora 214 — Night Sessions",
    "Blue Hour, Part 2",
    "Static Garden",
    "Low Tide",
    "Room 9",
    "Paper Lantern",
    "Harbour Lights",
    "The Long Way",
    "Salt & Smoke",
    "Quiet Signal",
    "Copper Field",
    "Nightcall 04",
    "Second Floor",
    "Overcast",
    "Winter Reel",
    "Meridian Passage",
    "Thin Ice",
    "The Understudy",
)

COMMENTS = (
    "Wrong performer tagged here I think.",
    "Playback stutters around 12:00 on the TV.",
    "Best in the library, no notes.",
    "Can we get the 2160p version of this one?",
    "Audio is out of sync from the start.",
    "Marker labels are off by one scene.",
    "The sound mix is the best of the whole Aurora run.",
    "Second half is much stronger than the first.",
)

FILE_BYTES = 512 * 1024


class AlreadyPopulatedError(RuntimeError):
    pass


async def seed(session: AsyncSession, library: Path) -> dict[str, int]:
    existing = await session.scalar(select(func.count()).select_from(Media))
    if existing:
        raise AlreadyPopulatedError(
            f"This database already holds {existing} titles. "
            "Refusing to add invented ones — drop the volume first if this is a demo server."
        )

    counts: dict[str, int] = {}

    admin = User(username=ADMIN, password_hash=hash_password(PASSWORD), role=UserRole.ADMIN)
    guests = [
        User(
            username=name,
            display_name=name.capitalize(),
            password_hash=hash_password(PASSWORD),
            role=UserRole.USER,
        )
        for name in GUESTS
    ]
    session.add_all([admin, *guests])
    await session.flush()
    everyone = [admin, *guests]
    counts["accounts"] = len(everyone)

    session.add_all(Studio(name=name, normalized_name=name.casefold()) for name in STUDIOS)
    performers = [Performer(name=name, normalized_name=name.casefold()) for name in PERFORMERS]
    tags = [Tag(name=name, normalized_name=name.casefold()) for name in TAGS]
    session.add_all([*performers, *tags])
    await session.flush()
    counts["performers"] = len(performers)
    counts["tags"] = len(tags)

    library.mkdir(parents=True, exist_ok=True)
    session.add(
        RootFolder(
            path=str(library),
            enabled=True,
            free_space_bytes=0,
            total_space_bytes=None,
        )
    )

    media: list[Media] = []
    for index, title in enumerate(TITLES):
        # A spread of dates so "added this week" and the recently-added row are
        # both non-empty and neither is everything.
        added = datetime.now(UTC) - timedelta(days=index * 3)
        item = Media(
            title=title,
            normalized_title=title.casefold(),
            studio=STUDIOS[index % len(STUDIOS)] if index % 5 else None,
            release_date=date(2024 + index % 3, 1 + index % 12, 1 + index % 28),
            # Left unset on a few, so "metadata matched" is a share rather than
            # 100% — the bar exists to show a gap.
            confidence=None if index % 6 == 0 else 0.6 + (index % 4) * 0.1,
        )
        path = library / f"{title.casefold().replace(' ', '-').replace('—', '-')}.mkv"
        path.write_bytes(b"\0" * FILE_BYTES)
        session.add(
            MediaFile(
                media=item,
                path=str(path),
                size=1_500_000_000 + index * 40_000_000,
                quality="2160p" if index % 3 == 0 else "1080p",
                resolution="3840x2160" if index % 3 == 0 else "1920x1080",
                duration_seconds=900 + index * 173,
                is_active=True,
            )
        )
        item.created_at = added
        media.append(item)
    await session.flush()
    counts["titles"] = len(media)

    for index, item in enumerate(media):
        for performer in random.sample(performers, k=random.choice([1, 2, 2, 3])):
            session.add(MediaPerformer(media_id=item.id, performer_id=performer.id))
        # A couple carry nothing, so the untagged count and the tag screen's
        # long tail are both real.
        if index % 7 != 0:
            for tag in random.sample(tags, k=random.choice([2, 3, 4])):
                session.add(
                    MediaTag(
                        media_id=item.id,
                        tag_id=tag.id,
                        confidence=0.6 + (index % 4) * 0.1,
                        source="scan" if index % 2 else "scraper",
                    )
                )

    ratings = 0
    for index, item in enumerate(media):
        # Not everything is rated, and the ones that are do not all agree —
        # a distribution with one bar is not a distribution.
        if index % 5 == 0:
            continue
        for voter in random.sample(everyone, k=random.choice([1, 2, 3, 4])):
            session.add(
                Rating(
                    user_id=voter.id,
                    media_id=item.id,
                    stars=random.choice([3, 4, 4, 5, 5, 5, 2]),
                )
            )
            ratings += 1
    counts["ratings"] = ratings

    remarks: list[Comment] = []
    for item, body in zip(media, COMMENTS, strict=False):
        comment = Comment(user_id=random.choice(everyone).id, media_id=item.id, body=body)
        session.add(comment)
        remarks.append(comment)
    await session.flush()
    counts["comments"] = len(remarks)

    for comment in remarks[:2]:
        session.add(
            CommentReport(
                comment_id=comment.id,
                reporter_id=random.choice(guests).id,
                reason="other",
            )
        )
    counts["reports"] = 2

    for index, item in enumerate(media[:5]):
        session.add(
            PlaybackProgress(
                user_id=admin.id,
                media_id=item.id,
                position_seconds=180 + index * 240,
                duration_seconds=900 + index * 173,
                device_label=("Living room TV", "iPad", "Desktop · Firefox")[index % 3],
                completed=False,
            )
        )
    counts["resuming"] = 5

    shorts = 0
    for index, item in enumerate(media[:8]):
        start = 300 + index * 90
        session.add(
            Short(
                media_id=item.id,
                title=f"{item.title.split(' ')[0]} — the good bit",
                start_seconds=start,
                end_seconds=start + 45 + index * 5,
                source=ShortSource.MARKER if index % 2 else ShortSource.HOTSPOT,
            )
        )
        shorts += 1
    counts["shorts"] = shorts

    late = Collection(
        owner_id=admin.id,
        name="Late shift",
        visibility=CollectionVisibility.SHARED,
    )
    private = Collection(
        owner_id=admin.id,
        name="Rewatch",
        visibility=CollectionVisibility.PRIVATE,
    )
    session.add_all([late, private])
    await session.flush()
    for item in media[:4]:
        session.add(CollectionItem(collection_id=late.id, media_id=item.id))
    for item in media[4:6]:
        session.add(CollectionItem(collection_id=private.id, media_id=item.id))
    counts["collections"] = 2

    for item in media[6:10]:
        session.add(WatchlistEntry(user_id=admin.id, media_id=item.id))
    counts["watchlist"] = 4

    # A queue with something in every state, so the cards and the three tabs are
    # all showing something.
    queued = 0
    for index, state in enumerate(
        (
            "downloading",
            "downloading",
            "queued",
            "queued",
            "queued",
            "moving",
            "completed",
            "failed",
        )
    ):
        job = DownloadJob(
            client_name="vault-b" if index % 2 else "inbox",
            protocol="usenet" if index % 2 else "torrent",
            release_guid=f"demo-release-{index}",
            status=state,
            priority=50 - index,
            size_bytes=2_000_000_000 + index * 100_000_000,
            remaining_bytes=None
            if state in {"completed", "failed"}
            else int((2_000_000_000 + index * 100_000_000) * (0.9 - index * 0.1)),
            download_speed_bytes=18_400_000 if state == "downloading" else None,
            estimated_seconds=252 + index * 60 if state != "completed" else None,
            error="no metadata match" if state == "failed" else None,
        )
        session.add(job)
        await session.flush()
        session.add(
            Request(
                user_id=random.choice(everyone).id,
                query=TITLES[(index + 10) % len(TITLES)],
                status=RequestStatus.DOWNLOADING,
                download_job_id=job.id,
            )
        )
        queued += 1
    counts["queue"] = queued

    await session.commit()
    return counts


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    library = Path(os.environ.get("DEMO_LIBRARY_PATH", "/data/library/demo"))

    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            counts = await seed(session, library)
    except AlreadyPopulatedError as refusal:
        print(refusal, file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print("Seeded a demo library:")
    for name, value in counts.items():
        print(f"  {value:>4} {name}")
    print(f"\nSign in as {ADMIN} (or any of {', '.join(GUESTS)}) with: {PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
