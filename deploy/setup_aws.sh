#!/usr/bin/env bash
# One-time AWS provisioning for the Photobooth S3 gallery.
#
# Creates:
#   - an S3 bucket configured for static website hosting with public-read objects
#   - an IAM user + least-privilege policy (put/get objects in that bucket only)
#   - an access key for that IAM user (printed once, never stored by AWS)
#
# Usage:
#   ./deploy/setup_aws.sh
#
# Re-running is safe: steps that already exist are skipped.

set -euo pipefail

BUCKET_NAME="${BUCKET_NAME:-mariocruz-photobooth-gallery}"
REGION="${REGION:-us-east-1}"
IAM_USER="${IAM_USER:-photobooth-uploader}"
PROFILE="${PROFILE:-PITA}"
POLICY_NAME="${IAM_USER}-s3-policy"

echo "== Photobooth AWS setup =="
echo "Bucket:  s3://${BUCKET_NAME}"
echo "Region:  ${REGION}"
echo "IAM user: ${IAM_USER}"
echo "Profile: ${PROFILE}"
echo

aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

# 1. Create the bucket (us-east-1 must omit LocationConstraint)
if aws_ s3api head-bucket --bucket "$BUCKET_NAME" >/dev/null 2>&1; then
  echo "[skip] bucket already exists"
else
  echo "[create] bucket"
  if [ "$REGION" = "us-east-1" ]; then
    aws_ s3api create-bucket --bucket "$BUCKET_NAME"
  else
    aws_ s3api create-bucket --bucket "$BUCKET_NAME" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
fi

# 2. Allow a public bucket policy (objects will be publicly readable — this is
#    the "shared public event gallery" you asked for; nothing is publicly listable
#    or writable, only readable via known URLs)
echo "[set] public access block (allow public read policy only)"
aws_ s3api put-public-access-block --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false

# 3. Static website hosting
echo "[set] static website hosting"
aws_ s3api put-bucket-website --bucket "$BUCKET_NAME" --website-configuration '{
  "IndexDocument": {"Suffix": "index.html"},
  "ErrorDocument": {"Key": "index.html"}
}'

# 4. Bucket policy: public GetObject only (no listing, no writing)
echo "[set] bucket policy (public read-only on objects)"
cat > /tmp/photobooth-bucket-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/*"
    }
  ]
}
EOF
aws_ s3api put-bucket-policy --bucket "$BUCKET_NAME" --policy file:///tmp/photobooth-bucket-policy.json
rm -f /tmp/photobooth-bucket-policy.json

# 5. CORS (harmless safety net for the gallery page fetching manifest.json)
echo "[set] CORS"
cat > /tmp/photobooth-cors.json <<EOF
{
  "CORSRules": [
    {
      "AllowedOrigins": ["*"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3600
    }
  ]
}
EOF
aws_ s3api put-bucket-cors --bucket "$BUCKET_NAME" --cors-configuration file:///tmp/photobooth-cors.json
rm -f /tmp/photobooth-cors.json

# 6. IAM user for the booth app to upload with (least privilege: this bucket only)
if aws_ iam get-user --user-name "$IAM_USER" >/dev/null 2>&1; then
  echo "[skip] IAM user already exists"
else
  echo "[create] IAM user"
  aws_ iam create-user --user-name "$IAM_USER" >/dev/null
fi

cat > /tmp/photobooth-iam-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UploadPhotosAndManifest",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/*"
    },
    {
      "Sid": "CheckManifestExists",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}"
    }
  ]
}
EOF
echo "[set] IAM inline policy (scoped to this bucket only)"
aws_ iam put-user-policy --user-name "$IAM_USER" --policy-name "$POLICY_NAME" \
  --policy-document file:///tmp/photobooth-iam-policy.json
rm -f /tmp/photobooth-iam-policy.json

# 7. Access key — only created if the user has none yet (AWS allows up to 2 keys/user)
EXISTING_KEYS=$(aws_ iam list-access-keys --user-name "$IAM_USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text)
if [ -n "$EXISTING_KEYS" ]; then
  echo "[skip] access key already exists for $IAM_USER (AccessKeyId: $EXISTING_KEYS)"
  echo "       delete it in IAM if you need a fresh secret, then re-run this script."
else
  echo "[create] access key"
  aws_ iam create-access-key --user-name "$IAM_USER" --output json > /tmp/photobooth-access-key.json
  ACCESS_KEY_ID=$(python3 -c "import json;print(json.load(open('/tmp/photobooth-access-key.json'))['AccessKey']['AccessKeyId'])")
  SECRET_ACCESS_KEY=$(python3 -c "import json;print(json.load(open('/tmp/photobooth-access-key.json'))['AccessKey']['SecretAccessKey'])")
  rm -f /tmp/photobooth-access-key.json

  echo
  echo "== Save these now — AWS will not show the secret again =="
  echo "AWS_ACCESS_KEY_ID=${ACCESS_KEY_ID}"
  echo "AWS_SECRET_ACCESS_KEY=${SECRET_ACCESS_KEY}"
  echo
fi

WEBSITE_ENDPOINT="${BUCKET_NAME}.s3-website-${REGION}.amazonaws.com"
echo "== Done =="
echo "Website endpoint: http://${WEBSITE_ENDPOINT}"
echo "Fill these into config.ini under [aws] and [website]."
