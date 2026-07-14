"""Async workers (pure functions with repository-mediated I/O).

M2 workers: analysis (transcript.v1 -> highlights.v1) and composer
(highlights.v1 -> timeline.v1). Side effects flow only through a
``ProjectRepository``, so the same functions run inline locally (CLI / tests)
and, later, wrapped as Lambda handlers reading S3 + DynamoDB in the ai-task lane.
"""
