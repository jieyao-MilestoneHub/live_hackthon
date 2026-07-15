# observability: a CloudWatch dashboard + a few alarms to PROVE the batch demo
# stays healthy ("no latency" = the architecture never throttles/backs up).
# Watch during the demo: Lambda Throttles/Concurrency (backend + workers), the
# analysis Step Functions duration/failures, DynamoDB throttles, and the
# analysis-intake SQS backlog. Batch render metrics are not standard CloudWatch
# metrics and are omitted (watch the Batch console job queue instead).

locals {
  # Metric rows for every backend + worker Lambda: [ns, metric, "FunctionName", fn].
  lambda_throttle_metrics = concat(
    [["AWS/Lambda", "Throttles", "FunctionName", var.backend_function_name]],
    [for fn in var.worker_function_names : ["AWS/Lambda", "Throttles", "FunctionName", fn]],
  )
  lambda_concurrency_metrics = concat(
    [["AWS/Lambda", "ConcurrentExecutions", "FunctionName", var.backend_function_name]],
    [for fn in var.worker_function_names : ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", fn]],
  )
}

resource "aws_cloudwatch_dashboard" "batch" {
  dashboard_name = "${var.name}-batch-upload"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title   = "Lambda Throttles (goal: 0)"
          region  = var.region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 60
          metrics = local.lambda_throttle_metrics
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title   = "Lambda ConcurrentExecutions"
          region  = var.region
          view    = "timeSeries"
          stat    = "Maximum"
          period  = 60
          metrics = local.lambda_concurrency_metrics
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Backend Lambda Duration / Errors"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", var.backend_function_name, { stat = "p95" }],
            ["AWS/Lambda", "Errors", "FunctionName", var.backend_function_name, { stat = "Sum" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Analysis Step Functions"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", var.analysis_state_machine_arn, { stat = "Sum" }],
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", var.analysis_state_machine_arn, { stat = "Sum" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", var.analysis_state_machine_arn, { stat = "Sum" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", var.analysis_state_machine_arn, { stat = "p95" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title  = "analysis-intake SQS backlog"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.intake_queue_name, { stat = "Maximum" }],
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", var.intake_queue_name, { stat = "Maximum" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = {
          title  = "DynamoDB throttles (goal: 0)"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/DynamoDB", "ThrottledRequests", "TableName", var.table_name, { stat = "Sum" }],
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", var.table_name, { stat = "Sum" }],
          ]
        }
      },
    ]
  })
}

# --- Alarms: the two signals that most directly mean "architecture is the
#     bottleneck". No SNS action wired (workshop simplicity) — visible in console
#     + usable by the load test / demo run.
resource "aws_cloudwatch_metric_alarm" "backend_throttles" {
  alarm_name          = "${var.name}-backend-throttles"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = var.backend_function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Backend upload control-plane is being throttled — raise Lambda concurrency / account quota."
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "analysis_failures" {
  alarm_name          = "${var.name}-analysis-failures"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = var.analysis_state_machine_arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Analysis pipeline executions are failing (e.g. transcription failures) under load."
  tags                = var.tags
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.batch.dashboard_name
}
