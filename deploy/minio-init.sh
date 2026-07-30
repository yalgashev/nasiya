#!/bin/sh
set -eu

fail() {
    printf '%s\n' "minio-init failed" >&2
    exit 1
}

run_quiet() {
    if ! "$@" >/dev/null 2>&1; then
        fail
    fi
}

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT is required}"
: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
: "${MINIO_BUCKET:?MINIO_BUCKET is required}"
: "${MINIO_APP_ACCESS_KEY:?MINIO_APP_ACCESS_KEY is required}"
: "${MINIO_APP_SECRET_KEY:?MINIO_APP_SECRET_KEY is required}"
: "${MINIO_APP_POLICY_NAME:?MINIO_APP_POLICY_NAME is required}"

case "$MINIO_BUCKET" in
    *[!a-z0-9.-]* | .* | *.)
        fail
        ;;
esac

policy_path="$(mktemp)"
trap 'rm -f "$policy_path"' EXIT HUP INT TERM

if ! printf '%s\n' \
    '{' \
    '  "Version": "2012-10-17",' \
    '  "Statement": [' \
    '    {' \
    '      "Sid": "NasiyaBucketScope",' \
    '      "Effect": "Allow",' \
    '      "Action": ["s3:GetBucketLocation", "s3:ListBucket"],' \
    "      \"Resource\": [\"arn:aws:s3:::$MINIO_BUCKET\"]" \
    '    },' \
    '    {' \
    '      "Sid": "NasiyaObjectScope",' \
    '      "Effect": "Allow",' \
    '      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],' \
    "      \"Resource\": [\"arn:aws:s3:::$MINIO_BUCKET/*\"]" \
    '    }' \
    '  ]' \
    '}' >"$policy_path"; then
    fail
fi

run_quiet mc alias set \
    nasiya-minio \
    "$MINIO_ENDPOINT" \
    "$MINIO_ROOT_USER" \
    "$MINIO_ROOT_PASSWORD" \
    --api S3v4
run_quiet mc ready nasiya-minio
run_quiet mc mb --ignore-existing "nasiya-minio/$MINIO_BUCKET"
run_quiet mc anonymous set private "nasiya-minio/$MINIO_BUCKET"
run_quiet mc admin policy create \
    nasiya-minio \
    "$MINIO_APP_POLICY_NAME" \
    "$policy_path"
run_quiet mc admin user add \
    nasiya-minio \
    "$MINIO_APP_ACCESS_KEY" \
    "$MINIO_APP_SECRET_KEY"
run_quiet mc admin user enable nasiya-minio "$MINIO_APP_ACCESS_KEY"
run_quiet mc admin policy attach \
    nasiya-minio \
    "$MINIO_APP_POLICY_NAME" \
    --user "$MINIO_APP_ACCESS_KEY"
run_quiet mc admin user info nasiya-minio "$MINIO_APP_ACCESS_KEY"

if ! anonymous_status="$(
    mc anonymous get "nasiya-minio/$MINIO_BUCKET" 2>/dev/null
)"; then
    fail
fi
case "$anonymous_status" in
    *'`private`'*)
        ;;
    *)
        fail
        ;;
esac
