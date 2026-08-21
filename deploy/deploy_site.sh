#!/usr/bin/env bash
# Publish website/ (the static gallery page) to the S3 bucket root.
# Safe to re-run any time the HTML/CSS/JS changes -- does NOT touch
# event photos or manifests (no --delete, and they live in other prefixes).
#
# If the gallery is fronted by CloudFront (see setup_https.sh), the edge
# cache is invalidated too -- otherwise visitors keep the old page for
# hours and a fix appears not to have deployed.

set -euo pipefail

BUCKET_NAME="${BUCKET_NAME:-mariocruz-photobooth-gallery}"
REGION="${REGION:-us-east-1}"
PROFILE="${PROFILE:-PITA}"
SUBDOMAIN="${SUBDOMAIN:-booth.mariothemaker.com}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBSITE_DIR="$SCRIPT_DIR/../website"

aws --profile "$PROFILE" --region "$REGION" s3 sync "$WEBSITE_DIR" "s3://${BUCKET_NAME}" \
  --cache-control "no-cache"

DIST_ID=$(aws --profile "$PROFILE" cloudfront list-distributions \
  --query "DistributionList.Items[?contains(Aliases.Items || \`[]\`, '${SUBDOMAIN}')].Id | [0]" \
  --output text 2>/dev/null || echo "None")

if [ -n "$DIST_ID" ] && [ "$DIST_ID" != "None" ]; then
  echo "Invalidating CloudFront cache ($DIST_ID)…"
  # Only the site shell -- photos and thumbnails are immutable, so leaving
  # them cached at the edge is exactly what we want.
  aws --profile "$PROFILE" cloudfront create-invalidation --distribution-id "$DIST_ID" \
    --paths "/" "/index.html" "/app.js" "/style.css" \
    --query 'Invalidation.{Id:Id,Status:Status}' --output text
  echo "Deployed to https://${SUBDOMAIN}"
else
  echo "Deployed to http://${BUCKET_NAME}.s3-website-${REGION}.amazonaws.com"
fi
