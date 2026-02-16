#!/bin/bash

# Test a slug against Greenhouse and Lever APIs
# Usage: ./test-slug.sh gensler
# or: ./test-slug.sh "foster-and-partners"

if [ -z "$1" ]; then
  echo "Usage: $0 <slug>"
  echo "Example: $0 gensler"
  echo "Example: $0 foster-and-partners"
  exit 1
fi

SLUG=$1

echo "Testing slug: $SLUG"
echo "================================================"
echo ""

echo "Greenhouse API:"
echo "  URL: https://boards-api.greenhouse.io/v1/boards/$SLUG/jobs"
GH_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "https://boards-api.greenhouse.io/v1/boards/$SLUG/jobs")
echo "  Status: $GH_RESPONSE"
if [ "$GH_RESPONSE" = "200" ]; then
  GH_COUNT=$(curl -s "https://boards-api.greenhouse.io/v1/boards/$SLUG/jobs" | jq '.jobs | length')
  echo "  Jobs found: $GH_COUNT"
  echo "  ✓ FOUND on Greenhouse"
elif [ "$GH_RESPONSE" = "404" ]; then
  echo "  ✗ Not found (404)"
else
  echo "  × Error or no response"
fi

echo ""
echo "Lever API:"
echo "  URL: https://api.lever.co/v0/postings/$SLUG?mode=json"
LV_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" "https://api.lever.co/v0/postings/$SLUG?mode=json")
echo "  Status: $LV_RESPONSE"
if [ "$LV_RESPONSE" = "200" ]; then
  LV_COUNT=$(curl -s "https://api.lever.co/v0/postings/$SLUG?mode=json" | jq 'length')
  echo "  Postings found: $LV_COUNT"
  echo "  ✓ FOUND on Lever"
elif [ "$LV_RESPONSE" = "404" ]; then
  echo "  ✗ Not found (404)"
else
  echo "  × Error or no response"
fi

echo ""
