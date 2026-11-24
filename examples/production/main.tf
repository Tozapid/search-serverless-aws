# Production: your own domain, digests by mail, no public sign-in and no
# reset. Pin the module to a release tag; a newer release is a change of ref.
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "eu-central-1"
}

# CloudFront and its certificate live in us-east-1.
provider "aws" {
  alias  = "global"
  region = "us-east-1"
}

module "search" {
  source = "git::https://github.com/Tozapid/search-serverless-aws.git?ref=v1.4.0"

  providers = {
    aws        = aws
    aws.global = aws.global
  }

  name              = "search"
  domain_name       = "search.example.com"
  route53_zone_name = "example.com" # "" if the DNS is elsewhere, see README
  admin_email       = "you@example.com"

  # On a domain verified in SES in this region.
  sender_address = "search@example.com"

  max_searches_per_user = 20
  ai_monthly_limit_usd  = 0.5
}

output "site_url" {
  description = "Address of the page."
  value       = module.search.site_url
}
