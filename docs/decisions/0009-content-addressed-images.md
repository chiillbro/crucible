# 0009. Content-addressed images, kept by default

## Context
Rebuilding images is the dominant cost of a run, and BuildKit assigns a new image id on
every build even when every layer is cached. Results must also say exactly what was tested.

## Decision
Image tags are `crucible/<task>-<role>:<digest>` where the digest covers the build context
(path, executable bit, bytes of every file) and, for derived images, the parent image id.
The backend reuses an existing tag instead of rebuilding. Images are kept after a run;
`--rm-images` deletes the ones a run built and `--rebuild` forces fresh builds. Containers,
by contrast, are always removed.

## Consequences
Editing a task yields new tags, so stale images cannot be mistaken for current ones, and
`results.json` records the tags. Keeping images trades disk for speed, which is the right
default for iterating on a task; CI and one-off runs can pass `--rm-images`. A change in an
upstream base image is not detected by the digest; use `--rebuild` to pick it up.
