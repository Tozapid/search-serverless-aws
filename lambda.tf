resource "aws_cloudwatch_log_group" "lambda" {
  #checkov:skip=CKV_AWS_158:CloudWatch's own encryption is enough for these logs. A customer managed key would add a monthly charge.
  #checkov:skip=CKV_AWS_338:Thirty days is enough to look into a missed digest and keeps the log bill small.
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 30
}

# The code without the tests. Content sources give every file the same time,
# so the zip changes only with the code.
data "archive_file" "app" {
  type = "zip"
  source {
    content  = file("${path.module}/lambda/search.py")
    filename = "search.py"
  }
  source {
    content  = file("${path.module}/lambda/engines.py")
    filename = "engines.py"
  }
  output_path      = "${path.module}/.build/search.zip"
  output_file_mode = "0644"
}

# tzdata, which zoneinfo needs and the Lambda image lacks, and PySocks for
# SOCKS proxies, as a layer: the wheels of lambda/requirements.txt for the
# function's platform (Linux arm64), fetched
# with pip while Terraform plans. The runner needs python3 and access to PyPI;
# the same requirements give the same zip, so the layer changes only with them.
data "external" "layer" {
  program = ["python3", "${path.module}/lambda/build_layer.py"]
  query = {
    requirements = "${path.module}/lambda/requirements.txt"
    target       = "${path.module}/.build/layer"
    python       = local.python
  }
}

data "archive_file" "layer" {
  type             = "zip"
  source_dir       = data.external.layer.result.dir
  output_path      = "${path.module}/.build/search_layer.zip"
  output_file_mode = "0644"
}

resource "aws_lambda_layer_version" "deps" {
  layer_name               = "${local.name}-deps"
  description              = "tzdata for the schedules of the searches, PySocks for the proxies"
  filename                 = data.archive_file.layer.output_path
  source_code_hash         = data.archive_file.layer.output_base64sha256
  compatible_runtimes      = ["python${local.python}"]
  compatible_architectures = ["arm64"]
}

resource "aws_lambda_function" "app" {
  #checkov:skip=CKV_AWS_50:X-Ray is disabled to avoid extra cost on a low-traffic function.
  #checkov:skip=CKV_AWS_115:Reserved concurrency is left to the account pool.
  #checkov:skip=CKV_AWS_116:A failed run is written to the history of its search and to the logs; the next one comes on schedule.
  #checkov:skip=CKV_AWS_117:The function calls search engines on the internet and public AWS endpoints.
  #checkov:skip=CKV_AWS_173:Environment values are names, addresses and prices, not secrets.
  #checkov:skip=CKV_AWS_272:Code signing is not used for this internal function.
  function_name = local.name
  role          = aws_iam_role.lambda.arn
  runtime       = "python${local.python}"
  architectures = ["arm64"]
  handler       = "search.handler"
  memory_size   = 256
  # A search reads its engines (10 s each at most) and waits for its AI
  # digest; a combined digest does that for every search of the user, four at
  # a time.
  timeout = 300

  filename         = data.archive_file.app.output_path
  source_code_hash = data.archive_file.app.output_base64sha256
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME           = aws_dynamodb_table.main.name
      SITE_URL             = local.site_url
      USER_POOL_ID         = aws_cognito_user_pool.main.id
      ADMIN_GROUP          = aws_cognito_user_group.admins.name
      SENDER               = var.sender_address
      CONFIGURATION_SET    = local.mail ? aws_sesv2_configuration_set.digests[0].configuration_set_name : ""
      MAIL_ENABLED         = tostring(local.mail)
      DEMO                 = tostring(var.demo)
      VERSION              = local.version
      RELEASES_REPOSITORY  = var.releases_repository
      TICK_MINUTES         = tostring(var.tick_minutes)
      COLLECT_LEAD_MINUTES = tostring(var.collect_lead_minutes)
      MAX_SEARCHES         = tostring(var.max_searches_per_user)
      AI_ENABLED           = tostring(var.ai_enabled)
      AI_MODEL             = var.ai_model
      AI_REGION            = var.ai_region
      AI_PRICE_INPUT       = tostring(var.ai_price_per_mtok.input)
      AI_PRICE_OUTPUT      = tostring(var.ai_price_per_mtok.output)
      AI_MONTHLY_LIMIT     = tostring(var.ai_monthly_limit_usd)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda.name
  }

  depends_on = [aws_iam_role_policy.lambda]

  lifecycle {
    precondition {
      condition     = !local.mail || try(data.aws_sesv2_email_identity.sender[0].verified_for_sending_status, false)
      error_message = "SES may not send from the domain of sender_address yet: verify it in SES first."
    }
  }
}

# A run is not retried: a second try could send the same digest twice. The
# next one comes on schedule, and an error is in the history of the search.
resource "aws_lambda_function_event_invoke_config" "app" {
  function_name          = aws_lambda_function.app.function_name
  maximum_retry_attempts = 0
}

# The tick: every tick_minutes the function sends the mail that is due and
# starts the next collection of the queue.
resource "aws_cloudwatch_event_rule" "tick" {
  name                = "${local.name}-tick"
  description         = "Sends the mail that is due and starts the next collection of the queue."
  schedule_expression = "rate(${var.tick_minutes} minutes)"
}

resource "aws_cloudwatch_event_target" "tick" {
  rule  = aws_cloudwatch_event_rule.tick.name
  arn   = aws_lambda_function.app.arn
  input = jsonencode({ action = "tick" })
}

resource "aws_lambda_permission" "tick" {
  statement_id  = "AllowEventBridgeTick"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.tick.arn
}
