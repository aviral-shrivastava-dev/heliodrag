variable "account_id" {
  description = "Cloudflare account that owns the bucket."
  type        = string
}

variable "bucket_name" {
  description = "R2 bucket holding the bronze Iceberg tables."
  type        = string
  default     = "starlink-drag-atlas"
}

variable "location" {
  description = "R2 location hint. Keep it near whatever runs the pipeline."
  type        = string
  default     = "WEUR"
}

variable "superseded_retention_days" {
  description = <<-EOT
    Days to keep data files that the current Iceberg snapshot no longer
    references.

    Bronze is append-only (ADR-0005), so a re-run leaves the previous copy of a
    partition behind permanently. Nothing in the pipeline removes it, which
    means storage grows with every backfill rather than with the data. This is
    the only thing standing between a few re-runs and an unbounded bill.

    Long enough to time-travel to a previous snapshot when investigating; short
    enough that a repeated backfill does not accumulate forever.
  EOT
  type        = number
  default     = 30
}

variable "abort_incomplete_upload_days" {
  description = <<-EOT
    Days before an incomplete multipart upload is abandoned.

    A backfill killed mid-write -- which has happened -- leaves parts that are
    billed as storage but belong to no object and are invisible in a listing.
  EOT
  type        = number
  default     = 7
}
