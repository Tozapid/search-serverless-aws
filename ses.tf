# With mail, the digests go out with this configuration set. It turns off the
# engagement metrics of Virtual Deliverability Manager for them and has no
# CLICK events, so SES leaves the links alone instead of sending them through
# awstrack.me.
resource "aws_sesv2_configuration_set" "digests" {
  count = local.mail ? 1 : 0

  configuration_set_name = local.name

  reputation_options {
    reputation_metrics_enabled = true
  }

  vdm_options {
    dashboard_options {
      engagement_metrics = "DISABLED"
    }
  }
}

# Opens only: SES adds an invisible image to each digest and counts the
# messages it was loaded from in CloudWatch (namespace AWS/SES, metric Open,
# dimension stream = digest). Clicks are not tracked, so the links stay as
# they are. One dimension value keeps it to a single custom metric.
resource "aws_sesv2_configuration_set_event_destination" "opens" {
  count = local.mail ? 1 : 0

  configuration_set_name = aws_sesv2_configuration_set.digests[0].configuration_set_name
  event_destination_name = "opens"

  event_destination {
    enabled              = true
    matching_event_types = ["OPEN"]

    cloud_watch_destination {
      dimension_configuration {
        dimension_name          = "stream"
        dimension_value_source  = "MESSAGE_TAG"
        default_dimension_value = "digest"
      }
    }
  }
}
