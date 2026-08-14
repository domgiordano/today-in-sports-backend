"""
Environment-derived constants.

Table names and the admin identity are injected by Terraform via
`local.lambda_variables` in today-in-sports-infrastructure/terraform/locals.tf.
Defaults here match the Terraform naming so a local run works without a full
environment.
"""

import os

PRODUCT = "today-in-sports"
APP_NAME = os.environ.get("APP_NAME", PRODUCT)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Required by the request-logging helper ported from xomify. Not provisioned in
# phase 1 — there are no public routes yet, so there is no request volume worth
# logging — but the helper reads it at import time.
REQUEST_LOG_TABLE_NAME = os.environ.get("REQUEST_LOG_TABLE_NAME", f"{APP_NAME}-request-log")

# Tables
GAMES_TABLE_NAME = os.environ.get("GAMES_TABLE_NAME", f"{APP_NAME}-games")
EVENTS_TABLE_NAME = os.environ.get("EVENTS_TABLE_NAME", f"{APP_NAME}-events")
QUESTIONS_TABLE_NAME = os.environ.get("QUESTIONS_TABLE_NAME", f"{APP_NAME}-questions")
QUIZZES_TABLE_NAME = os.environ.get("QUIZZES_TABLE_NAME", f"{APP_NAME}-quizzes")
SOURCE_RUNS_TABLE_NAME = os.environ.get("SOURCE_RUNS_TABLE_NAME", f"{APP_NAME}-source-runs")
# Play sessions: one per identity per quiz date. Holds server-stamped timing and
# graded answers -- the client never posts a score.
PLAYS_TABLE_NAME = os.environ.get("PLAYS_TABLE_NAME", f"{APP_NAME}-plays")
USERS_TABLE_NAME = os.environ.get("USERS_TABLE_NAME", f"{APP_NAME}-users")
GROUPS_TABLE_NAME = os.environ.get("GROUPS_TABLE_NAME", f"{APP_NAME}-groups")
GROUPS_INVITE_INDEX = os.environ.get("GROUPS_INVITE_INDEX", "invite-index")
ANNOUNCEMENTS_TABLE_NAME = os.environ.get(
    "ANNOUNCEMENTS_TABLE_NAME", f"{APP_NAME}-announcements")
STATS_TABLE_NAME = os.environ.get("STATS_TABLE_NAME", f"{APP_NAME}-stats")

# Indexes
QUESTIONS_STATUS_INDEX = os.environ.get("QUESTIONS_STATUS_INDEX", "status-mmdd-index")
QUESTIONS_BANK_INDEX = os.environ.get("QUESTIONS_BANK_INDEX", "status-sportTier-index")
QUIZZES_STATUS_INDEX = os.environ.get("QUIZZES_STATUS_INDEX", "status-quizDate-index")
EVENTS_SPORT_INDEX = os.environ.get("EVENTS_SPORT_INDEX", "sport-year-index")
EVENTS_NOTABILITY_INDEX = os.environ.get("EVENTS_NOTABILITY_INDEX", "sport-notability-index")

RAW_ARCHIVE_BUCKET = os.environ.get("RAW_ARCHIVE_BUCKET", "")

# Identity
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_JWKS_URL = os.environ.get("COGNITO_JWKS_URL", "")

# Review + assembly
QUIZ_LENGTH = 5
DEFAULT_ASSEMBLE_DAYS = 30
MAX_ASSEMBLE_DAYS = 90

VALID_QUESTION_STATUSES = ("draft", "approved", "rejected", "used")
# `held` is a veto, and it exists because publishing is automatic. A denied day
# left as `draft` would simply be republished by the next morning's cron, so
# refusing a day has to be a state the publisher can see rather than the absence
# of one. Recycling reassembles the day and returns it to `draft`.
VALID_QUIZ_STATUSES = ("draft", "scheduled", "published", "held")
