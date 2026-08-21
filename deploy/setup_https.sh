#!/usr/bin/env bash
# Put the gallery behind HTTPS on a custom domain, via ACM + CloudFront.
#
# Why this exists beyond a nicer URL: navigator.share() -- the API that
# gives phones a one-tap "Save photo" button that drops straight into the
# camera roll -- only works in a secure context. Over plain HTTP the
# gallery has to fall back to telling people to press-and-hold the image.
# HTTPS turns the button on by itself (see website/app.js).
#
# Creates:
#   - an ACM certificate for $SUBDOMAIN (DNS-validated in Route53)
#   - a CloudFront distribution fronting the bucket's S3 website endpoint
#   - A/AAAA alias records pointing $SUBDOMAIN at that distribution
#
# Re-running is safe: existing pieces are detected and reused.
#
# Usage:
#   ./deploy/setup_https.sh                       # uses the defaults below
#   SUBDOMAIN=booth.example.com ./deploy/setup_https.sh
#
# Afterwards, point the booth at it:
#   config.ini -> [website] base_url = https://$SUBDOMAIN

set -euo pipefail

SUBDOMAIN="${SUBDOMAIN:-booth.mariothemaker.com}"
HOSTED_ZONE_DOMAIN="${HOSTED_ZONE_DOMAIN:-mariothemaker.com}"
BUCKET_NAME="${BUCKET_NAME:-mariocruz-photobooth-gallery}"
REGION="${REGION:-us-east-1}"
PROFILE="${PROFILE:-PITA}"

# CloudFront only reads certificates from us-east-1, whatever the bucket region.
CERT_REGION="us-east-1"
# Fixed CloudFront zone id used by every Route53 alias to a distribution.
CF_ZONE_ID="Z2FDTNDATAQYW2"
# AWS managed cache policy "CachingOptimized".
CACHE_POLICY_ID="658327ea-f89d-4fab-a63d-7e88639e58f6"

aws_() { aws --profile "$PROFILE" "$@"; }

echo "== HTTPS setup for ${SUBDOMAIN} =="

ZONE_ID=$(aws_ route53 list-hosted-zones-by-name --dns-name "$HOSTED_ZONE_DOMAIN" \
  --query "HostedZones[?Name=='${HOSTED_ZONE_DOMAIN}.'].Id | [0]" --output text | sed 's|/hostedzone/||')
[ "$ZONE_ID" = "None" ] && { echo "No Route53 zone for $HOSTED_ZONE_DOMAIN"; exit 1; }
echo "Hosted zone: $ZONE_ID"

# 1. Certificate ---------------------------------------------------------
CERT_ARN=$(aws_ acm list-certificates --region "$CERT_REGION" \
  --query "CertificateSummaryList[?DomainName=='${SUBDOMAIN}'].CertificateArn | [0]" --output text)

if [ "$CERT_ARN" = "None" ] || [ -z "$CERT_ARN" ]; then
  echo "[create] certificate"
  CERT_ARN=$(aws_ acm request-certificate --region "$CERT_REGION" \
    --domain-name "$SUBDOMAIN" --validation-method DNS --query CertificateArn --output text)
  sleep 8  # give ACM a moment to publish the validation record it wants
else
  echo "[skip] certificate exists"
fi

STATUS=$(aws_ acm describe-certificate --region "$CERT_REGION" --certificate-arn "$CERT_ARN" \
  --query Certificate.Status --output text)

if [ "$STATUS" != "ISSUED" ]; then
  echo "[set] DNS validation record"
  RR=$(aws_ acm describe-certificate --region "$CERT_REGION" --certificate-arn "$CERT_ARN" \
    --query 'Certificate.DomainValidationOptions[0].ResourceRecord' --output json)
  VNAME=$(printf '%s' "$RR" | python3 -c 'import json,sys;print(json.load(sys.stdin)["Name"])')
  VVALUE=$(printf '%s' "$RR" | python3 -c 'import json,sys;print(json.load(sys.stdin)["Value"])')
  aws_ route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch "{
    \"Changes\": [{\"Action\": \"UPSERT\", \"ResourceRecordSet\": {
      \"Name\": \"${VNAME}\", \"Type\": \"CNAME\", \"TTL\": 300,
      \"ResourceRecords\": [{\"Value\": \"${VVALUE}\"}]}}]}" >/dev/null
  echo "[wait] certificate validation (usually a couple of minutes)"
  aws_ acm wait certificate-validated --region "$CERT_REGION" --certificate-arn "$CERT_ARN"
fi
echo "Certificate: ISSUED"

# 2. CloudFront ----------------------------------------------------------
DIST_ID=$(aws_ cloudfront list-distributions \
  --query "DistributionList.Items[?contains(Aliases.Items || \`[]\`, '${SUBDOMAIN}')].Id | [0]" --output text)

if [ "$DIST_ID" = "None" ] || [ -z "$DIST_ID" ]; then
  echo "[create] CloudFront distribution"
  # http-only to the origin: S3 *website* endpoints (needed for index docs
  # and redirects) speak HTTP only. Viewer traffic is still HTTPS-only.
  CONFIG=$(cat <<EOF
{
  "CallerReference": "photobooth-gallery-${SUBDOMAIN}",
  "Aliases": {"Quantity": 1, "Items": ["${SUBDOMAIN}"]},
  "DefaultRootObject": "index.html",
  "Comment": "Photo booth gallery (${SUBDOMAIN}) -> ${BUCKET_NAME}",
  "Enabled": true,
  "HttpVersion": "http2and3",
  "IsIPV6Enabled": true,
  "PriceClass": "PriceClass_100",
  "Origins": {"Quantity": 1, "Items": [{
    "Id": "s3-website-gallery",
    "DomainName": "${BUCKET_NAME}.s3-website-${REGION}.amazonaws.com",
    "CustomOriginConfig": {
      "HTTPPort": 80, "HTTPSPort": 443,
      "OriginProtocolPolicy": "http-only",
      "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
      "OriginReadTimeout": 30, "OriginKeepaliveTimeout": 5},
    "ConnectionAttempts": 3, "ConnectionTimeout": 10}]},
  "DefaultCacheBehavior": {
    "TargetOriginId": "s3-website-gallery",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
      "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
    "Compress": true,
    "CachePolicyId": "${CACHE_POLICY_ID}"},
  "ViewerCertificate": {
    "ACMCertificateArn": "${CERT_ARN}",
    "SSLSupportMethod": "sni-only",
    "MinimumProtocolVersion": "TLSv1.2_2021"}
}
EOF
)
  DIST_ID=$(printf '%s' "$CONFIG" | aws_ cloudfront create-distribution \
    --distribution-config file:///dev/stdin --query Distribution.Id --output text)
else
  echo "[skip] distribution exists"
fi

DIST_DOMAIN=$(aws_ cloudfront get-distribution --id "$DIST_ID" --query Distribution.DomainName --output text)
echo "Distribution: $DIST_ID ($DIST_DOMAIN)"

# 3. DNS -----------------------------------------------------------------
echo "[set] ${SUBDOMAIN} alias records"
aws_ route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch "{
  \"Comment\": \"${SUBDOMAIN} -> photo booth gallery CloudFront\",
  \"Changes\": [
    {\"Action\": \"UPSERT\", \"ResourceRecordSet\": {\"Name\": \"${SUBDOMAIN}.\", \"Type\": \"A\",
      \"AliasTarget\": {\"HostedZoneId\": \"${CF_ZONE_ID}\", \"DNSName\": \"${DIST_DOMAIN}.\", \"EvaluateTargetHealth\": false}}},
    {\"Action\": \"UPSERT\", \"ResourceRecordSet\": {\"Name\": \"${SUBDOMAIN}.\", \"Type\": \"AAAA\",
      \"AliasTarget\": {\"HostedZoneId\": \"${CF_ZONE_ID}\", \"DNSName\": \"${DIST_DOMAIN}.\", \"EvaluateTargetHealth\": false}}}
  ]}" >/dev/null

echo "[wait] distribution deploy (first time takes several minutes)"
aws_ cloudfront wait distribution-deployed --id "$DIST_ID"

echo
echo "== Done =="
echo "Gallery: https://${SUBDOMAIN}"
echo "Set [website] base_url = https://${SUBDOMAIN} in config.ini on the booth."
echo
echo "After changing website/ files, deploy them and invalidate the cache:"
echo "  ./deploy/deploy_site.sh"
echo "  aws --profile ${PROFILE} cloudfront create-invalidation --distribution-id ${DIST_ID} --paths '/*'"
