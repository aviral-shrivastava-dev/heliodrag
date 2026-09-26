# ADR-0011: SeaweedFS replaces MinIO as the local S3 server

- **Status:** accepted
- **Date:** 2026-09-26
- **Supersedes:** the choice of MinIO in Phase 4 and in
  [ADR-0010](0010-one-lake-module-for-every-storage-backend.md). The rest of
  ADR-0010 stands: nothing in the pipeline changed, only the server.

## Context

The Docker stack rehearsed Cloudflare R2 with MinIO, pinned to an exact
release on quay.io. The first CI run of the new S3 job failed in under a
second, with `docker run` exiting 125. Asked directly, both registries now
refuse the images: quay.io's MinIO repositories require a login, and Docker
Hub's `minio/minio` returns 404. MinIO stopped publishing free images for its
community edition in 2025, and the pinned releases went with the rest.

The stack had only kept working on the development machine because Docker had
cached the images during Phase 4. Anyone following the README on a fresh
machine would have failed at the first command.

## Decision

SeaweedFS `4.47`, in its all-in-one `weed mini` mode:

- one container serves S3 on port 8333 and a file browser on 8888;
- it creates the bucket on start from `S3_BUCKET`, so the separate bucket-
  creating container is gone;
- it enforces the keys given in `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
  -- checked by making a request with a wrong key and watching it refused;
- local-only defaults are `atlas` / `atlas-local-only`.

The full S3 suite passes against it, and so does a backfill run inside the
stack: the same 48 space-weather rows and 39,712 element sets as the MinIO run,
and a `dbt build` of 98 of 98.

## Alternatives considered

- **Chainguard's rebuild of MinIO.** The smallest change, but its free tier
  offers only the moving `latest` tag -- the kind of dependency that changes
  underneath a pipeline.
- **RustFS.** A near drop-in MinIO replacement, but a young project.
- **`bitnamilegacy/minio`.** Frozen, and named as legacy by its publisher.
- **Garage.** Needs a cluster layout configured before it serves anything.

## Consequences

- SeaweedFS is Apache-2.0 and publishes numbered releases, so the pin is
  meaningful. Pinning did not protect MinIO, though -- its whole repository was
  withdrawn. The real protection is that CI now pulls this image on every push,
  so a withdrawal is noticed within a day rather than by a newcomer.
- Ports and defaults changed: S3 moved from 9000 to 8333 and the MinIO console
  on 9001 is replaced by SeaweedFS's file browser on 8888.
- No pipeline code changed. Everything talks S3 through `starlink_drag.lake`,
  which is the point of having one place that knows where the lake is.
