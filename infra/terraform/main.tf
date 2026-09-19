# The bronze lake.
#
# One bucket. Everything else the project needs -- the warehouse, dbt's
# artefacts, the figures -- is derived and rebuildable, so it is not worth
# managing. This is the only storage whose loss would cost hours of fetching
# against a hard rate limit.

resource "cloudflare_r2_bucket" "lake" {
  account_id = var.account_id
  name       = var.bucket_name
  location   = var.location

  # R2 has no object-lock equivalent to enforce this, so immutability is a
  # property of how the pipeline writes (never in place) rather than of the
  # bucket. See ADR-0004.
}

resource "cloudflare_r2_bucket_lifecycle" "lake" {
  account_id  = var.account_id
  bucket_name = cloudflare_r2_bucket.lake.name

  rules = [
    {
      id      = "expire-superseded-iceberg-files"
      enabled = true

      conditions = {
        prefix = "bronze/"
      }

      # Iceberg never deletes a superseded data file; it stops referencing it.
      # Without this the lake grows with every re-run rather than with the data.
      delete_objects_transition = {
        condition = {
          type = "Age"
          # Provider takes seconds.
          maxAge = var.superseded_retention_days * 24 * 60 * 60
        }
      }
    },
    {
      id      = "abandon-incomplete-uploads"
      enabled = true

      abort_multipart_uploads_transition = {
        condition = {
          type   = "Age"
          maxAge = var.abort_incomplete_upload_days * 24 * 60 * 60
        }
      }
    },
  ]
}
