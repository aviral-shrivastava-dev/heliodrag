terraform {
  required_version = ">= 1.9"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }

  # State is local by default so this can be read and reasoned about without an
  # account. Before anyone else runs it, move state to R2 itself -- two people
  # applying against local state will fight over the bucket.
  #
  # backend "s3" {
  #   bucket                      = "starlink-drag-atlas-tfstate"
  #   key                         = "terraform.tfstate"
  #   region                      = "auto"
  #   endpoints                   = { s3 = "https://<account>.r2.cloudflarestorage.com" }
  #   skip_credentials_validation = true
  #   skip_region_validation      = true
  #   skip_requesting_account_id  = true
  #   skip_s3_checksum            = true
  #   use_path_style              = true
  # }
}

provider "cloudflare" {
  # Read from CLOUDFLARE_API_TOKEN. Never write a token into a .tf file: this
  # directory is committed.
}
