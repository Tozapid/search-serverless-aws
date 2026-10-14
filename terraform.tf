terraform {
  # 1.10 brings the S3 lockfile (use_lockfile).
  required_version = ">= 1.10.0"

  # Partial configuration: bucket, key and region come from terraform init
  # (see README). The S3 lockfile replaces a DynamoDB lock table.
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
    # Runs lambda/build_layer.py while planning, for the layer of search.py.
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
