# The demo kept its resources at the top of the state until the repository
# became a module (v1.3.0): its state keeps every resource, now under
# module.search.

moved {
  from = aws_cloudwatch_log_delivery_source.access
  to   = module.search.aws_cloudwatch_log_delivery_source.access
}

moved {
  from = aws_cloudwatch_log_delivery_destination.access
  to   = module.search.aws_cloudwatch_log_delivery_destination.access
}

moved {
  from = aws_cloudwatch_log_delivery.access
  to   = module.search.aws_cloudwatch_log_delivery.access
}

moved {
  from = aws_apigatewayv2_api.http
  to   = module.search.aws_apigatewayv2_api.http
}

moved {
  from = aws_apigatewayv2_authorizer.cognito
  to   = module.search.aws_apigatewayv2_authorizer.cognito
}

moved {
  from = aws_apigatewayv2_integration.lambda
  to   = module.search.aws_apigatewayv2_integration.lambda
}

moved {
  from = aws_apigatewayv2_route.api
  to   = module.search.aws_apigatewayv2_route.api
}

moved {
  from = aws_cloudwatch_log_group.api
  to   = module.search.aws_cloudwatch_log_group.api
}

moved {
  from = aws_apigatewayv2_stage.default
  to   = module.search.aws_apigatewayv2_stage.default
}

moved {
  from = aws_lambda_permission.api
  to   = module.search.aws_lambda_permission.api
}

moved {
  from = aws_cloudfront_origin_access_control.web
  to   = module.search.aws_cloudfront_origin_access_control.web
}

moved {
  from = aws_cloudfront_cache_policy.web
  to   = module.search.aws_cloudfront_cache_policy.web
}

moved {
  from = aws_cloudfront_response_headers_policy.web
  to   = module.search.aws_cloudfront_response_headers_policy.web
}

moved {
  from = aws_cloudfront_distribution.web
  to   = module.search.aws_cloudfront_distribution.web
}

moved {
  from = aws_cognito_user_pool.main
  to   = module.search.aws_cognito_user_pool.main
}

moved {
  from = aws_cognito_user_pool_client.web
  to   = module.search.aws_cognito_user_pool_client.web
}

moved {
  from = aws_cognito_user.admin
  to   = module.search.aws_cognito_user.admin
}

moved {
  from = aws_cognito_user_group.admins
  to   = module.search.aws_cognito_user_group.admins
}

moved {
  from = aws_cognito_user_in_group.admin
  to   = module.search.aws_cognito_user_in_group.admin
}

moved {
  from = aws_acm_certificate.web
  to   = module.search.aws_acm_certificate.web
}

moved {
  from = aws_route53_record.certificate
  to   = module.search.aws_route53_record.certificate
}

moved {
  from = aws_acm_certificate_validation.web
  to   = module.search.aws_acm_certificate_validation.web
}

moved {
  from = aws_route53_record.web
  to   = module.search.aws_route53_record.web
}

moved {
  from = aws_dynamodb_table.main
  to   = module.search.aws_dynamodb_table.main
}

moved {
  from = aws_iam_role.lambda
  to   = module.search.aws_iam_role.lambda
}

moved {
  from = aws_iam_role_policy.lambda
  to   = module.search.aws_iam_role_policy.lambda
}

moved {
  from = aws_cloudwatch_log_group.lambda
  to   = module.search.aws_cloudwatch_log_group.lambda
}

moved {
  from = aws_lambda_layer_version.deps
  to   = module.search.aws_lambda_layer_version.deps
}

moved {
  from = aws_lambda_function.app
  to   = module.search.aws_lambda_function.app
}

moved {
  from = aws_lambda_function_event_invoke_config.app
  to   = module.search.aws_lambda_function_event_invoke_config.app
}

moved {
  from = aws_cloudwatch_event_rule.tick
  to   = module.search.aws_cloudwatch_event_rule.tick
}

moved {
  from = aws_cloudwatch_event_target.tick
  to   = module.search.aws_cloudwatch_event_target.tick
}

moved {
  from = aws_lambda_permission.tick
  to   = module.search.aws_lambda_permission.tick
}

moved {
  from = aws_ssm_parameter.admin_password
  to   = module.search.aws_ssm_parameter.admin_password
}

moved {
  from = aws_cloudwatch_log_group.reset
  to   = module.search.aws_cloudwatch_log_group.reset
}

moved {
  from = aws_iam_role.reset
  to   = module.search.aws_iam_role.reset
}

moved {
  from = aws_iam_role_policy.reset
  to   = module.search.aws_iam_role_policy.reset
}

moved {
  from = aws_lambda_function.reset
  to   = module.search.aws_lambda_function.reset
}

moved {
  from = aws_cloudwatch_event_rule.reset
  to   = module.search.aws_cloudwatch_event_rule.reset
}

moved {
  from = aws_cloudwatch_event_target.reset
  to   = module.search.aws_cloudwatch_event_target.reset
}

moved {
  from = aws_lambda_permission.reset
  to   = module.search.aws_lambda_permission.reset
}

moved {
  from = aws_lambda_invocation.first_reset
  to   = module.search.aws_lambda_invocation.first_reset
}

moved {
  from = aws_s3_bucket.web
  to   = module.search.aws_s3_bucket.web
}

moved {
  from = aws_s3_bucket_public_access_block.web
  to   = module.search.aws_s3_bucket_public_access_block.web
}

moved {
  from = aws_s3_bucket_ownership_controls.web
  to   = module.search.aws_s3_bucket_ownership_controls.web
}

moved {
  from = aws_s3_bucket_server_side_encryption_configuration.web
  to   = module.search.aws_s3_bucket_server_side_encryption_configuration.web
}

moved {
  from = aws_s3_bucket_lifecycle_configuration.web
  to   = module.search.aws_s3_bucket_lifecycle_configuration.web
}

moved {
  from = aws_s3_bucket_policy.web
  to   = module.search.aws_s3_bucket_policy.web
}

moved {
  from = aws_sesv2_configuration_set.digests
  to   = module.search.aws_sesv2_configuration_set.digests
}

moved {
  from = aws_sesv2_configuration_set_event_destination.opens
  to   = module.search.aws_sesv2_configuration_set_event_destination.opens
}

moved {
  from = aws_s3_object.web
  to   = module.search.aws_s3_object.web
}

moved {
  from = aws_s3_object.config
  to   = module.search.aws_s3_object.config
}
