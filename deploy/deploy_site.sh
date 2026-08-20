#!/usr/bin/env bash
# Publish website/ (the static gallery page) to the S3 bucket root.
# Safe to re-run any time the HTML/CSS/JS changes -- does NOT touch
# event photos or manifests (no --delete, and they live in other prefixes).

set -euo pipefail

BUCKET_NAME="${BUCKET_NAME:-mariocruz-photobooth-gallery}"
REGION="${REGION:-us-east-1}"
PROFILE="${PROFILE:-PITA}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBSITE_DIR="$SCRIPT_DIR/../website"

aws --profile "$PROFILE" --region "$REGION" s3 sync "$WEBSITE_DIR" "s3://${BUCKET_NAME}" \
  --cache-control "no-cache"

echo "Deployed to http://${BUCKET_NAME}.s3-website-${REGION}.amazonaws.com"
