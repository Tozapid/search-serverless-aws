data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  statement {
    sid = "Table"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      # An administrator lists the searches of every user.
      "dynamodb:Scan",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      aws_dynamodb_table.main.arn,
      "${aws_dynamodb_table.main.arn}/index/*",
    ]
  }

  statement {
    sid = "ManageUsers"
    actions = [
      "cognito-idp:ListUsers",
      "cognito-idp:ListUsersInGroup",
      "cognito-idp:AdminGetUser",
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminDeleteUser",
      "cognito-idp:AdminDisableUser",
      "cognito-idp:AdminEnableUser",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminSetUserMFAPreference",
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminRemoveUserFromGroup",
    ]
    resources = [aws_cognito_user_pool.main.arn]
  }

  # The tick hands each collection of the queue, and the page each "run
  # now", to an invocation of its own.
  statement {
    sid       = "RunSearches"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.function_arn]
  }

  dynamic "statement" {
    for_each = local.mail ? [1] : []
    content {
      sid     = "SendDigests"
      actions = ["ses:SendEmail", "ses:SendRawEmail"]
      # The sender domain, in the SES sandbox the verified recipients, and the
      # configuration set of the digests.
      resources = [
        "arn:aws:ses:${var.region}:${local.account_id}:identity/*",
        aws_sesv2_configuration_set.digests[0].arn,
      ]

      condition {
        test     = "StringEquals"
        variable = "ses:FromAddress"
        values   = [var.sender_address]
      }
    }
  }

  # While the account is in the SES sandbox, a recipient has to be verified;
  # the page asks SES to send the link and shows whether it was followed.
  dynamic "statement" {
    for_each = local.mail ? [1] : []
    content {
      sid = "Recipients"
      actions = [
        "ses:GetAccount",
        "ses:GetEmailIdentity",
        "ses:VerifyEmailIdentity",
      ]
      #checkov:skip=CKV_AWS_356:GetAccount and VerifyEmailIdentity have no resource to name.
      resources = ["*"]
    }
  }

  # The inference profile of ai_model and the model in every Region the
  # profile may route the request to.
  dynamic "statement" {
    for_each = var.ai_enabled ? [1] : []
    content {
      sid     = "Digest"
      actions = ["bedrock:InvokeModel"]
      resources = [
        "arn:aws:bedrock:${var.ai_region}:${local.account_id}:inference-profile/${var.ai_model}",
        "arn:aws:bedrock:*::foundation-model/${local.ai_base_model}",
        "arn:aws:bedrock:::foundation-model/${local.ai_base_model}",
      ]
    }
  }

  # A third-party model such as Claude is sold through AWS Marketplace: the
  # first call subscribes the account to it, which needs these, and only for
  # its product. Amazon's own models need nothing of the kind.
  dynamic "statement" {
    for_each = var.ai_enabled && var.ai_marketplace_product_id != "" ? [1] : []
    content {
      sid     = "DigestModelSubscription"
      actions = ["aws-marketplace:ViewSubscriptions", "aws-marketplace:Subscribe"]
      #checkov:skip=CKV_AWS_356:Marketplace subscriptions have no resource ARN; the condition names the product.
      resources = ["*"]

      condition {
        test     = "StringEquals"
        variable = "aws-marketplace:ProductId"
        values   = [var.ai_marketplace_product_id]
      }
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "lambda" {
  name   = local.name
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}
