# The page on its own domain: a certificate and, with a Route 53 zone, its
# validation and the alias records. Without a zone the validation records
# are in the output certificate_validation_records, and the name gets a CNAME
# to the CloudFront address at its DNS host; the first apply waits up to 30
# minutes for the certificate.

data "aws_route53_zone" "web" {
  count        = local.custom_domain && var.route53_zone_name != "" ? 1 : 0
  name         = var.route53_zone_name
  private_zone = false
}

resource "aws_acm_certificate" "web" {
  count             = local.custom_domain ? 1 : 0
  provider          = aws.global
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "certificate" {
  for_each = length(data.aws_route53_zone.web) == 0 ? {} : {
    for option in aws_acm_certificate.web[0].domain_validation_options : option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }

  allow_overwrite = true
  zone_id         = data.aws_route53_zone.web[0].zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 60
  records         = [each.value.record]
}

resource "aws_acm_certificate_validation" "web" {
  count                   = local.custom_domain ? 1 : 0
  provider                = aws.global
  certificate_arn         = aws_acm_certificate.web[0].arn
  validation_record_fqdns = length(data.aws_route53_zone.web) == 0 ? null : [for record in aws_route53_record.certificate : record.fqdn]

  timeouts {
    create = "30m"
  }
}

resource "aws_route53_record" "web" {
  for_each = length(data.aws_route53_zone.web) == 0 ? toset([]) : toset(["A", "AAAA"])

  allow_overwrite = true
  zone_id         = data.aws_route53_zone.web[0].zone_id
  name            = var.domain_name
  type            = each.value

  alias {
    name                   = aws_cloudfront_distribution.web.domain_name
    zone_id                = aws_cloudfront_distribution.web.hosted_zone_id
    evaluate_target_health = false
  }
}
