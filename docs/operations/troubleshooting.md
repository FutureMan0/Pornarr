# Troubleshooting

## Imports copy instead of hardlinking

Downloads and the library are on different filesystems. Check the startup warning and
the health check. Fix the volume mounts; there is no configuration that works around
it. See [deployment](deployment.md).

## Transcoding runs on CPU although a GPU is present

Check the detected acceleration methods in the administration area. If the list is
empty, the device was not passed through, the container user lacks access to it, or
the driver libraries are not mounted. For NVIDIA this is almost always a missing
container toolkit.

## Playback fails with TRANSCODE_LIMIT_REACHED

Every hardware and software session slot is in use. Consumer GPUs cap concurrent
encode sessions at driver level. Raise the software limit, lower the per-user limit,
or accept the cap — the error is deliberate, because exceeding the driver limit
produces an unreadable FFmpeg failure instead.

## Search returns nothing from one indexer

Look at its health status and last error. Three consecutive failures mark an indexer
unhealthy for five minutes and it is skipped. A wrong API key shows as an
authentication error; a wrong category shows as an empty but successful response.

## Everything lands in quarantine

No metadata provider key is configured, so nothing scores above 0.4 confidence, and a
filter rule is set to quarantine below that threshold. Either configure a provider or
lower the threshold. This is expected behaviour on a fresh instance.

## Progress never updates

The event stream is being buffered by the reverse proxy. See the proxy section in
[deployment](deployment.md). The symptom is a progress bar that jumps only on page
reload.

## Downloads complete but nothing is imported

Check that the download client's category matches what Pornarr expects, and that the
path the client reports is visible to the worker at the same path. A client running
outside the container reports host paths, which the worker cannot resolve.
