# 0006 — HLS with hardware acceleration from v1

Status: accepted (2026-08-07)

## Context
Playback options ranged from direct play only, through software HLS, to full hardware acceleration. Hardware acceleration is the largest single cost item in the project.

## Decision
HLS with VAAPI, QSV and NVENC detection ships in v1.

## Consequences
The support matrix — driver version, device passthrough, container permissions, per-generation codec support — cannot be fully covered in CI. Mitigations: capability detection at startup, software fallback on every path, and an explicit support statement in the documentation. The reference development machine is an RTX 3060, which encodes H.264 and HEVC but not AV1.
