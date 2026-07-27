#!/bin/sh
set -eu

: "${WORKBENCH_S3_ENDPOINT:?WORKBENCH_S3_ENDPOINT is required}"
: "${WORKBENCH_S3_BUCKET:?WORKBENCH_S3_BUCKET is required}"
: "${WORKBENCH_S3_ACCESS_KEY:?WORKBENCH_S3_ACCESS_KEY is required}"
: "${WORKBENCH_S3_SECRET_KEY:?WORKBENCH_S3_SECRET_KEY is required}"

payload=/tmp/workbench-object-smoke.txt
roundtrip=/tmp/workbench-object-roundtrip.txt
printf '%s\n' 'material-workbench-object-smoke-v1' > "$payload"
digest="$(sha256sum "$payload" | cut -d ' ' -f 1)"
object_key="smoke/sha256/$digest.txt"

mc alias set workbench "$WORKBENCH_S3_ENDPOINT" "$WORKBENCH_S3_ACCESS_KEY" "$WORKBENCH_S3_SECRET_KEY" >/dev/null

if ! mc stat "workbench/$WORKBENCH_S3_BUCKET/$object_key" >/dev/null 2>&1; then
  mc pipe \
    --attr "Content-Type=text/plain;content-digest=sha256:$digest" \
    "workbench/$WORKBENCH_S3_BUCKET/$object_key" < "$payload" >/dev/null
fi

mc cat "workbench/$WORKBENCH_S3_BUCKET/$object_key" > "$roundtrip"
roundtrip_digest="$(sha256sum "$roundtrip" | cut -d ' ' -f 1)"
test "$roundtrip_digest" = "$digest"

size_bytes="$(wc -c < "$roundtrip" | tr -d ' ')"
printf 'Object smoke passed: key=%s digest=sha256:%s size=%s\n' "$object_key" "$digest" "$size_bytes"
