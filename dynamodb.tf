# One table; the data model is in the docstring of lambda/search.py. An active
# search (or a combined digest in place of its user's searches) also carries
# due = "1" and next_run, the time of its mail, so the sparse index "due"
# lists exactly the mail to collect and send.
resource "aws_dynamodb_table" "main" {
  #checkov:skip=CKV_AWS_119:An AWS owned key avoids a dedicated KMS charge.
  #checkov:skip=CKV_AWS_28:Point-in-time recovery is on outside the demo, which is rebuilt every hour.
  name         = local.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "due"
    type = "S"
  }

  attribute {
    name = "next_run"
    type = "S"
  }

  global_secondary_index {
    name = "due"
    key_schema {
      attribute_name = "due"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "next_run"
      key_type       = "RANGE"
    }
    projection_type = "KEYS_ONLY"
  }

  ttl {
    attribute_name = "expires"
    enabled        = true
  }

  # The demo is rebuilt every hour; a backup of it has nothing to bring back.
  point_in_time_recovery {
    enabled = !var.demo
  }

  server_side_encryption {
    enabled = true
  }
}
