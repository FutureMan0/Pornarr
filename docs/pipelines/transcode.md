# Playback and transcoding

## Direct play first

`GET /api/media/{id}/playback-info` compares the file's container, video codec, audio
codec, profile and level against what the client reports it can play. When they match,
the original file is streamed over HTTP range requests and no GPU is involved. This is
the common case and the reason the session limits are rarely reached.

## Sessions

Transcode sessions live in Redis with a sixty-second TTL, refreshed by a heartbeat
from the player every fifteen seconds.

```
session_id, user_id, media_id, pid, variant, started_at,
last_heartbeat, mode (hw|sw)
```

Sixty seconds without a heartbeat kills FFmpeg and expires the segments. A nightly
cleanup job removes orphaned segment directories.

## Limits

Detected at startup, overridable in configuration:

- `max_hw_sessions` — bounded by the driver. Consumer NVIDIA cards cap concurrent
  NVENC sessions, and exceeding the cap produces an opaque FFmpeg failure rather than
  a useful error, which is why the limit is enforced before starting.
- `max_sw_sessions` — CPU cores divided by two.
- `max_per_user` — two by default.

A saturated GPU falls back to software encoding. Both saturated returns 429 with
`TRANSCODE_LIMIT_REACHED`, never a raw FFmpeg error.

## Hardware

VAAPI, QSV and NVENC are detected at startup by probing the render device and running
a capability check. What the machine actually supports is shown in the administration
area, because "why is it transcoding on CPU" is otherwise unanswerable.

The reference development machine has an RTX 3060: H.264 and HEVC encoding, no AV1
encoding, AV1 decoding available.
