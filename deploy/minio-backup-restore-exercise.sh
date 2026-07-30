#!/bin/sh
set -eu

MC_IMAGE="quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
stage="CONFIG"

fail() {
    printf '%s\n' "STORAGE_BACKUP_RESTORE_FAILED code=$stage" >&2
    exit 1
}

if [ -z "${MINIO_ENDPOINT:-}" ] \
    || [ -z "${MINIO_ROOT_USER:-}" ] \
    || [ -z "${MINIO_ROOT_PASSWORD:-}" ] \
    || [ -z "${MINIO_BUCKET:-}" ]; then
    fail
fi

case "$MINIO_BUCKET" in
    *[!a-z0-9.-]* | .* | *.)
        fail
        ;;
esac

MINIO_DOCKER_NETWORK="${MINIO_DOCKER_NETWORK:-host}"
export MINIO_ENDPOINT MINIO_ROOT_USER MINIO_ROOT_PASSWORD

backup_parent="${M8_STORAGE_BACKUP_PARENT:-$PWD}"
if [ ! -d "$backup_parent" ]; then
    fail
fi
exercise_dir="$(mktemp -d "$backup_parent/.m8-storage-backup.XXXXXX")"
object_id="$(tr -d '-' </proc/sys/kernel/random/uuid)"
object_key="v1/objects/$object_id.png"
source_bucket="nasiya-backup-source-$object_id"
restore_bucket="nasiya-restore-$object_id"
source_file="$exercise_dir/source.png"
restored_file="$exercise_dir/restored.png"
mirror_dir="$exercise_dir/mirror"
mkdir "$mirror_dir"

run_mc() {
    docker run --rm \
        --network "$MINIO_DOCKER_NETWORK" \
        --entrypoint /bin/sh \
        --env MINIO_ENDPOINT \
        --env MINIO_ROOT_USER \
        --env MINIO_ROOT_PASSWORD \
        --volume "$exercise_dir:/exercise" \
        "$MC_IMAGE" \
        -eu -c '
            mc alias set exercise \
                "$MINIO_ENDPOINT" \
                "$MINIO_ROOT_USER" \
                "$MINIO_ROOT_PASSWORD" \
                --api S3v4 >/dev/null 2>&1
            exec mc "$@"
        ' sh "$@"
}

run_quiet() {
    if ! run_mc "$@" >/dev/null 2>&1; then
        fail
    fi
}

cleanup() {
    run_mc rb --force "exercise/$source_bucket" \
        >/dev/null 2>&1 || true
    run_mc rb --force "exercise/$restore_bucket" \
        >/dev/null 2>&1 || true
    find "$exercise_dir" -mindepth 1 -delete >/dev/null 2>&1 || true
    rmdir "$exercise_dir" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

stage="SOURCE_WRITE"
if ! printf '%s' \
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=' \
    | base64 -d >"$source_file"; then
    fail
fi
source_checksum="$(sha256sum "$source_file" | awk '{print $1}')"

stage="READY"
run_quiet ready exercise
run_quiet stat "exercise/$MINIO_BUCKET"
stage="CONFIGURED_PRIVACY"
if ! configured_anonymous_status="$(
    run_mc anonymous get "exercise/$MINIO_BUCKET" 2>/dev/null
)"; then
    fail
fi
case "$configured_anonymous_status" in
    *'`private`'*)
        ;;
    *)
        fail
        ;;
esac
stage="SOURCE_BUCKET"
run_quiet mb --ignore-existing "exercise/$source_bucket"
run_quiet anonymous set private "exercise/$source_bucket"
stage="SEED_COPY"
run_quiet cp \
    --quiet \
    --attr "checksum-sha256=$source_checksum" \
    /exercise/source.png \
    "exercise/$source_bucket/$object_key"
stage="BACKUP"
run_quiet mirror \
    --quiet \
    "exercise/$source_bucket" \
    /exercise/mirror
stage="RESTORE_BUCKET"
run_quiet mb --ignore-existing "exercise/$restore_bucket"
run_quiet anonymous set private "exercise/$restore_bucket"
stage="RESTORE"
run_quiet mirror \
    --quiet \
    --attr "checksum-sha256=$source_checksum" \
    /exercise/mirror \
    "exercise/$restore_bucket"

stage="SOURCE_COUNT"
if ! source_listing="$(
    run_mc ls --recursive --json "exercise/$source_bucket" 2>/dev/null
)"; then
    fail
fi
stage="RESTORE_COUNT"
if ! restore_listing="$(
    run_mc ls --recursive --json "exercise/$restore_bucket" 2>/dev/null
)"; then
    fail
fi
source_count="$(
    printf '%s\n' "$source_listing" | awk 'NF { count += 1 } END { print count + 0 }'
)"
backup_count="$(
    find "$mirror_dir" -type f | awk 'END { print NR + 0 }'
)"
restore_count="$(
    printf '%s\n' "$restore_listing" | awk 'NF { count += 1 } END { print count + 0 }'
)"
stage="COUNT_MATCH"
if [ "$source_count" -lt 1 ] \
    || [ "$source_count" -ne "$backup_count" ] \
    || [ "$source_count" -ne "$restore_count" ]; then
    fail
fi

stage="METADATA"
if ! restored_stat="$(
    run_mc stat --json "exercise/$restore_bucket/$object_key" 2>/dev/null
)"; then
    fail
fi
normalized_stat="$(printf '%s' "$restored_stat" | tr '[:upper:]' '[:lower:]')"
case "$normalized_stat" in
    *checksum-sha256*"$source_checksum"*)
        ;;
    *)
        fail
        ;;
esac
case "$normalized_stat" in
    *image/png*)
        ;;
    *)
        fail
        ;;
    esac

stage="CONTENT"
if ! run_mc cat "exercise/$restore_bucket/$object_key" \
    >"$restored_file" 2>/dev/null; then
    fail
fi
if ! cmp -s "$source_file" "$restored_file"; then
    fail
fi

stage="PRIVACY"
if ! anonymous_status="$(
    run_mc anonymous get "exercise/$restore_bucket" 2>/dev/null
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

printf '%s\n' \
    "STORAGE_BACKUP_RESTORE_PASS source=$source_count backup=$backup_count restored=$restore_count checksum=VERIFIED privacy=PRIVATE"
