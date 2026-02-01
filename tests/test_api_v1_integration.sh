#!/bin/bash
# Integration tests for REST API v1 endpoints (Issue #6)
# Run this script after starting docker-compose to verify the API works

set -e

BASE_URL="${BASE_URL:-http://localhost:5000}"
TEST_FILE="${1:-tests/test_chinese_simple.md}"

echo "======================================"
echo "API v1 Integration Tests"
echo "======================================"
echo "Base URL: $BASE_URL"
echo ""

# Test 1: List available formats
echo "Test 1: GET /api/v1/formats"
echo "--------------------------------------"
FORMATS_RESPONSE=$(curl -s "$BASE_URL/api/v1/formats")
echo "$FORMATS_RESPONSE" | jq '.' || echo "ERROR: Invalid JSON response"

INPUT_COUNT=$(echo "$FORMATS_RESPONSE" | jq '.input_formats | length')
OUTPUT_COUNT=$(echo "$FORMATS_RESPONSE" | jq '.output_formats | length')
echo "✓ Found $INPUT_COUNT input formats and $OUTPUT_COUNT output formats"
echo ""

# Test 2: Submit conversion job with Pandoc
echo "Test 2: POST /api/v1/convert (Pandoc engine)"
echo "--------------------------------------"

if [ ! -f "$TEST_FILE" ]; then
    echo "Creating test file..."
    echo "# Test Document" > /tmp/test_api.md
    echo "This is a test for the API." >> /tmp/test_api.md
    TEST_FILE="/tmp/test_api.md"
fi

CONVERT_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/convert" \
    -F "file=@$TEST_FILE" \
    -F "to_format=docx" \
    -F "engine=pandoc")

echo "$CONVERT_RESPONSE" | jq '.' || echo "ERROR: Invalid JSON response"

JOB_ID=$(echo "$CONVERT_RESPONSE" | jq -r '.job_id')
STATUS_URL=$(echo "$CONVERT_RESPONSE" | jq -r '.status_url')

if [ "$JOB_ID" == "null" ] || [ -z "$JOB_ID" ]; then
    echo "✗ FAILED: No job_id in response"
    exit 1
fi

echo "✓ Job submitted: $JOB_ID"
echo "✓ Status URL: $STATUS_URL"
echo ""

# Test 3: Check job status (polling)
echo "Test 3: GET /api/v1/status/{job_id}"
echo "--------------------------------------"

MAX_ATTEMPTS=30
ATTEMPT=0
STATUS="pending"

while [ "$STATUS" != "success" ] && [ "$STATUS" != "failure" ] && [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    sleep 2
    ATTEMPT=$((ATTEMPT + 1))

    STATUS_RESPONSE=$(curl -s "$BASE_URL$STATUS_URL")
    STATUS=$(echo "$STATUS_RESPONSE" | jq -r '.status')
    PROGRESS=$(echo "$STATUS_RESPONSE" | jq -r '.progress')

    echo "Attempt $ATTEMPT: Status=$STATUS, Progress=$PROGRESS%"
done

echo ""
echo "Final status response:"
echo "$STATUS_RESPONSE" | jq '.' || echo "ERROR: Invalid JSON response"

if [ "$STATUS" == "success" ]; then
    echo "✓ Conversion completed successfully"

    DOWNLOAD_URL=$(echo "$STATUS_RESPONSE" | jq -r '.download_url')
    IS_MULTIFILE=$(echo "$STATUS_RESPONSE" | jq -r '.is_multifile')
    FILE_COUNT=$(echo "$STATUS_RESPONSE" | jq -r '.file_count')

    echo "✓ Download URL: $DOWNLOAD_URL"
    echo "✓ Multi-file: $IS_MULTIFILE"
    echo "✓ File count: $FILE_COUNT"
    echo ""

    # Test 4: Download converted file
    echo "Test 4: GET /api/v1/download/{job_id}"
    echo "--------------------------------------"

    OUTPUT_FILE="/tmp/api_test_output_${JOB_ID}.docx"
    HTTP_CODE=$(curl -s -w "%{http_code}" -o "$OUTPUT_FILE" "$BASE_URL$DOWNLOAD_URL")

    if [ "$HTTP_CODE" == "200" ]; then
        FILE_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null)
        echo "✓ File downloaded successfully"
        echo "✓ File size: $FILE_SIZE bytes"
        echo "✓ Saved to: $OUTPUT_FILE"
    else
        echo "✗ FAILED: Download failed with HTTP $HTTP_CODE"
        exit 1
    fi

elif [ "$STATUS" == "failure" ]; then
    ERROR_MSG=$(echo "$STATUS_RESPONSE" | jq -r '.error')
    echo "✗ FAILED: Conversion failed - $ERROR_MSG"
    exit 1
else
    echo "✗ FAILED: Timeout waiting for conversion"
    exit 1
fi

echo ""

# Test 5: Error handling - Invalid format
echo "Test 5: Error Handling - Invalid to_format"
echo "--------------------------------------"

ERROR_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/convert" \
    -F "file=@$TEST_FILE" \
    -F "to_format=invalid_format")

HTTP_CODE=$(echo "$ERROR_RESPONSE" | jq -r '.error' >/dev/null 2>&1 && echo "422" || echo "unknown")
ERROR_MSG=$(echo "$ERROR_RESPONSE" | jq -r '.error')

echo "$ERROR_RESPONSE" | jq '.' || echo "Response: $ERROR_RESPONSE"

if echo "$ERROR_MSG" | grep -qi "unsupported"; then
    echo "✓ Correctly rejected invalid format"
else
    echo "✗ FAILED: Expected 'unsupported format' error"
fi

echo ""

# Test 6: Error handling - Missing file
echo "Test 6: Error Handling - Missing file"
echo "--------------------------------------"

ERROR_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/convert" \
    -F "to_format=pdf")

ERROR_MSG=$(echo "$ERROR_RESPONSE" | jq -r '.error')
echo "$ERROR_RESPONSE" | jq '.' || echo "Response: $ERROR_RESPONSE"

if echo "$ERROR_MSG" | grep -qi "file"; then
    echo "✓ Correctly rejected missing file"
else
    echo "✗ FAILED: Expected 'missing file' error"
fi

echo ""

# Test 7: Invalid UUID format
echo "Test 7: Error Handling - Invalid UUID"
echo "--------------------------------------"

ERROR_RESPONSE=$(curl -s "$BASE_URL/api/v1/status/not-a-valid-uuid")
ERROR_MSG=$(echo "$ERROR_RESPONSE" | jq -r '.error')

echo "$ERROR_RESPONSE" | jq '.' || echo "Response: $ERROR_RESPONSE"

if echo "$ERROR_MSG" | grep -qi "invalid"; then
    echo "✓ Correctly rejected invalid UUID"
else
    echo "✗ FAILED: Expected 'invalid' error"
fi

echo ""
echo "======================================"
echo "All API v1 Integration Tests Passed!"
echo "======================================"
