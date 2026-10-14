output "site_url" {
  description = "Address of the page"
  value       = local.site_url
}

output "cloudfront_url" {
  description = "CloudFront address of the page; with domain_name, the target of its CNAME at a DNS host other than Route 53"
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "certificate_validation_records" {
  description = "Records that validate the certificate of domain_name, to add by hand without route53_zone_name"
  value = local.custom_domain && var.route53_zone_name == "" ? [
    for option in aws_acm_certificate.web[0].domain_validation_options : {
      type  = option.resource_record_type
      name  = option.resource_record_name
      value = option.resource_record_value
    }
  ] : []
}

output "mail" {
  description = "Whether digests are mailed"
  value       = local.mail
}

output "user_pool_id" {
  description = "Cognito user pool of the page"
  value       = aws_cognito_user_pool.main.id
}

output "function_name" {
  description = "Lambda function of the API and the searches"
  value       = aws_lambda_function.app.function_name
}

output "table_name" {
  description = "DynamoDB table with the searches, their history and the settings"
  value       = aws_dynamodb_table.main.name
}
