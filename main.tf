provider "aws" {
  region = var.region

  default_tags {
    tags = var.tags
  }
}

# CloudFront and its certificates live in us-east-1.
provider "aws" {
  alias  = "global"
  region = "us-east-1"

  default_tags {
    tags = var.tags
  }
}

data "aws_caller_identity" "current" {}

locals {
  name       = var.name
  account_id = data.aws_caller_identity.current.account_id
  web_bucket = "${local.name}-web-${local.account_id}"

  # Mail is on with a sender address. Without one (the demo) a run does
  # everything but send, and nothing is made in SES.
  mail          = var.sender_address != ""
  sender_domain = local.mail ? split("@", var.sender_address)[1] : ""

  # The page on its own domain, or on the CloudFront address without one.
  custom_domain = var.domain_name != ""
  site_url      = local.custom_domain ? "https://${var.domain_name}" : "https://${aws_cloudfront_distribution.web.domain_name}"

  reset = var.reset_schedule != ""

  # The version of this deployment: the newest entry of CHANGELOG.md.
  version = regex("(?m)^## (v[0-9]+\\.[0-9]+\\.[0-9]+)(?:\\s|$)", file("${path.module}/CHANGELOG.md"))[0]

  # The model behind the inference profile: eu.amazon.x -> amazon.x.
  ai_base_model = replace(var.ai_model, "/^[a-z]+\\./", "")

  # The Python of the functions; the layer is built for the same one.
  python = "3.14"

  # Known before the function exists, so that its policy may let it invoke
  # itself: the tick hands every due search to its own invocation.
  function_arn = "arn:aws:lambda:${var.region}:${local.account_id}:function:${local.name}"

  csp = join("; ", [
    "default-src 'self'",
    # Only the two libraries of the page, not all of jsDelivr.
    "script-src 'self' https://cdn.jsdelivr.net/npm/amazon-cognito-identity-js@6.3.15/ https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/",
    "style-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self' https://cognito-idp.${var.region}.amazonaws.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ])
}

# The sender domain is verified in SES outside this stack; this one only
# sends from it.
data "aws_sesv2_email_identity" "sender" {
  count          = local.mail ? 1 : 0
  email_identity = local.sender_domain
}
