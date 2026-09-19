output "bucket_name" {
  description = "Set this as LAKE_BUCKET."
  value       = cloudflare_r2_bucket.lake.name
}

output "s3_endpoint" {
  description = "Set this as LAKE_ENDPOINT_URL. R2 speaks the S3 API."
  value       = "https://${var.account_id}.r2.cloudflarestorage.com"
}

output "next_steps" {
  description = "What Terraform cannot do for you."
  value       = <<-EOT
    Terraform does not create R2 access keys -- they are issued in the
    dashboard under R2 > Manage API Tokens, and creating them here would put
    them in state.

    Then set, in .env:
      LAKE_BACKEND=r2
      LAKE_BUCKET=${cloudflare_r2_bucket.lake.name}
      LAKE_ENDPOINT_URL=https://${var.account_id}.r2.cloudflarestorage.com
      LAKE_ACCESS_KEY_ID=...
      LAKE_SECRET_ACCESS_KEY=...
  EOT
}
