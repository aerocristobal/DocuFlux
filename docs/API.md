# DocuFlux REST API v1 Documentation

This document provides comprehensive documentation for the DocuFlux REST API v1, which enables programmatic document conversion and integration with external tools and workflows.

## Table of Contents

- [Overview](#overview)
- [Authentication](#authentication)
- [Rate Limiting](#rate-limiting)
- [Endpoints](#endpoints)
  - [POST /api/v1/convert](#post-apiv1convert)
  - [GET /api/v1/status/{job_id}](#get-apiv1statusjob_id)
  - [GET /api/v1/download/{job_id}](#get-apiv1downloadjob_id)
  - [GET /api/v1/formats](#get-apiv1formats)
- [Error Handling](#error-handling)
- [Examples](#examples)

## Overview

The DocuFlux REST API v1 provides:

- **Asynchronous document conversion** with job tracking
- **Multiple conversion engines**: Pandoc (universal) and Marker (AI-powered PDF)
- **Format auto-detection** for simplified integration
- **Multi-file output support** with automatic ZIP bundling
- **Real-time progress tracking** via polling

**Base URL**: `http://localhost:5000` (or your deployment URL)

**API Version**: v1

**Content Types**:
- Request: `multipart/form-data` or `application/json`
- Response: `application/json`

## Authentication

The API currently uses **IP-based rate limiting** without requiring authentication tokens. This provides a simple integration path while preventing abuse.

**Future versions** may support API key authentication for:
- Higher rate limits
- User-specific quotas
- Webhook callbacks
- Advanced features

## Rate Limiting

All API endpoints share the same rate limits as the web UI:

- **1000 requests per day** per IP address
- **200 requests per hour** per IP address

Rate limit headers are included in responses:

```
X-RateLimit-Limit: 200
X-RateLimit-Remaining: 195
X-RateLimit-Reset: 1612345678
```

When rate limit is exceeded, the API returns:

```json
HTTP/1.1 429 Too Many Requests
{
  "error": "Rate limit exceeded",
  "message": "200 per 1 hour"
}
```

## Endpoints

### POST /api/v1/convert

Submit a document conversion job.

**URL**: `/api/v1/convert`

**Method**: `POST`

**Content-Type**: `multipart/form-data`

**CSRF**: Exempt (REST API)

**Request Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | File | Yes | Document file to convert |
| `to_format` | String | Yes | Target format key (e.g., "markdown", "pdf", "docx") |
| `from_format` | String | No | Source format key, auto-detected from extension if omitted |
| `engine` | String | No | Conversion engine: "pandoc" or "marker" (default: "pandoc") |
| `force_ocr` | Boolean | No | Force OCR for Marker engine (default: false) |
| `use_llm` | Boolean | No | Use LLM for Marker engine (default: false) |

**Success Response (202 Accepted)**:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "status_url": "/api/v1/status/550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-02-01T19:42:10Z"
}
```

**Error Responses**:

| Code | Description | Example |
|------|-------------|---------|
| 400 | Missing required field | `{"error": "Missing required field: file"}` |
| 400 | No file selected | `{"error": "No file selected"}` |
| 400 | Missing to_format | `{"error": "Missing required field: to_format"}` |
| 422 | Unsupported format | `{"error": "Unsupported output format: xyz"}` |
| 422 | Invalid engine | `{"error": "Invalid engine: foo. Must be \"pandoc\" or \"marker\""}` |
| 422 | Cannot auto-detect format | `{"error": "Cannot auto-detect format from extension: .xyz"}` |
| 507 | Server storage full | `{"error": "Server storage full"}` |

**Example**:

```bash
# Basic conversion with auto-detection
curl -X POST http://localhost:5000/api/v1/convert \
  -F "file=@document.pdf" \
  -F "to_format=markdown"

# Conversion with Marker AI engine
curl -X POST http://localhost:5000/api/v1/convert \
  -F "file=@document.pdf" \
  -F "to_format=markdown" \
  -F "engine=marker" \
  -F "force_ocr=true"

# Explicit format specification
curl -X POST http://localhost:5000/api/v1/convert \
  -F "file=@document.md" \
  -F "from_format=markdown" \
  -F "to_format=docx" \
  -F "engine=pandoc"
```

---

### GET /api/v1/status/{job_id}

Check the status of a conversion job.

**URL**: `/api/v1/status/{job_id}`

**Method**: `GET`

**URL Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | UUID | Yes | Job identifier from `/api/v1/convert` response |

**Success Response (200 OK) - Pending**:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "progress": 0,
  "filename": "document.pdf",
  "from_format": "pdf",
  "to_format": "markdown",
  "engine": "pandoc",
  "created_at": "2026-02-01T19:42:10Z"
}
```

**Success Response (200 OK) - Processing**:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 45,
  "filename": "document.pdf",
  "from_format": "pdf_marker",
  "to_format": "markdown",
  "engine": "marker",
  "created_at": "2026-02-01T19:42:10Z",
  "started_at": "2026-02-01T19:42:15Z"
}
```

**Success Response (200 OK) - Completed**:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "success",
  "progress": 100,
  "filename": "document.pdf",
  "from_format": "pdf_marker",
  "to_format": "markdown",
  "engine": "marker",
  "created_at": "2026-02-01T19:42:10Z",
  "started_at": "2026-02-01T19:42:15Z",
  "completed_at": "2026-02-01T19:44:30Z",
  "download_url": "/api/v1/download/550e8400-e29b-41d4-a716-446655440000",
  "is_multifile": true,
  "file_count": 12,
  "metadata": {
    "pages": 10,
    "images_extracted": 5,
    "tables_detected": 2
  }
}
```

**Success Response (200 OK) - Failed**:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failure",
  "progress": 0,
  "filename": "document.pdf",
  "from_format": "pdf",
  "to_format": "markdown",
  "engine": "pandoc",
  "created_at": "2026-02-01T19:42:10Z",
  "started_at": "2026-02-01T19:42:15Z",
  "completed_at": "2026-02-01T19:42:45Z",
  "error": "Conversion failed: Invalid PDF structure"
}
```

**Error Responses**:

| Code | Description | Example |
|------|-------------|---------|
| 400 | Invalid UUID format | `{"error": "Invalid job ID format"}` |
| 404 | Job not found | `{"error": "Job not found"}` |

**Status Values**:

- `pending`: Job queued, waiting for worker
- `processing`: Conversion in progress
- `success`: Conversion completed successfully
- `failure`: Conversion failed

**Example**:

```bash
# Check job status
curl http://localhost:5000/api/v1/status/550e8400-e29b-41d4-a716-446655440000

# Poll for completion
while true; do
  STATUS=$(curl -s http://localhost:5000/api/v1/status/550e8400-... | jq -r '.status')
  if [ "$STATUS" == "success" ] || [ "$STATUS" == "failure" ]; then
    break
  fi
  sleep 2
done
```

---

### GET /api/v1/download/{job_id}

Download the converted file(s).

**URL**: `/api/v1/download/{job_id}`

**Method**: `GET`

**URL Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | UUID | Yes | Job identifier from completed job |

**Success Response (200 OK)**:

- **Single file**: Returns file with `Content-Disposition: attachment`
- **Multi-file**: Returns ZIP archive with all files

**Headers**:

```
Content-Type: application/octet-stream (single file)
Content-Type: application/zip (multi-file)
Content-Disposition: attachment; filename="document.md"
```

**Error Responses**:

| Code | Description | Example |
|------|-------------|---------|
| 400 | Invalid UUID | `{"error": "Invalid job ID format"}` |
| 404 | Job not found | `{"error": "Job not found"}` |
| 404 | Job not completed | `{"error": "Job not completed yet"}` |
| 410 | Files expired | `{"error": "Output files not found or expired"}` |

**Example**:

```bash
# Download single file
curl -O -J http://localhost:5000/api/v1/download/550e8400-e29b-41d4-a716-446655440000

# Download with custom filename
curl -o converted_document.md http://localhost:5000/api/v1/download/550e8400-...

# Download ZIP (multi-file)
curl -o conversion_result.zip http://localhost:5000/api/v1/download/550e8400-...
```

---

### GET /api/v1/formats

List all supported input/output formats and conversions.

**URL**: `/api/v1/formats`

**Method**: `GET`

**Success Response (200 OK)**:

```json
{
  "input_formats": [
    {
      "name": "PDF Document",
      "key": "pdf",
      "extension": ".pdf",
      "mime_types": ["application/pdf"],
      "supports_marker": true,
      "supports_pandoc": true
    },
    {
      "name": "Pandoc Markdown",
      "key": "markdown",
      "extension": ".md",
      "mime_types": ["text/markdown", "text/plain"],
      "supports_marker": false,
      "supports_pandoc": true
    }
  ],
  "output_formats": [
    {
      "name": "Pandoc Markdown",
      "key": "markdown",
      "extension": ".md"
    },
    {
      "name": "PDF via LaTeX",
      "key": "pdf",
      "extension": ".pdf"
    }
  ],
  "conversions": [
    {
      "from": "pdf",
      "to": "markdown",
      "engines": ["pandoc", "marker"],
      "recommended_engine": "marker"
    },
    {
      "from": "docx",
      "to": "markdown",
      "engines": ["pandoc"],
      "recommended_engine": "pandoc"
    }
  ]
}
```

**Example**:

```bash
# List all formats
curl http://localhost:5000/api/v1/formats | jq '.'

# List input formats only
curl http://localhost:5000/api/v1/formats | jq '.input_formats'

# Find formats supporting Marker
curl http://localhost:5000/api/v1/formats | jq '.input_formats[] | select(.supports_marker == true)'
```

## Error Handling

All error responses follow a consistent JSON structure:

```json
{
  "error": "Human-readable error message"
}
```

**Common Error Codes**:

- `400 Bad Request`: Invalid request parameters
- `404 Not Found`: Resource not found (job, file)
- `410 Gone`: Resource expired/deleted
- `413 Payload Too Large`: File exceeds 100MB limit
- `422 Unprocessable Entity`: Invalid format conversion
- `429 Too Many Requests`: Rate limit exceeded
- `507 Insufficient Storage`: Server disk full

**Error Response Examples**:

```json
// Missing field
{"error": "Missing required field: to_format"}

// Invalid format
{"error": "Unsupported output format: xyz"}

// Job not found
{"error": "Job not found"}

// Files expired
{"error": "Output files not found or expired"}

// Rate limit
{"error": "Rate limit exceeded", "message": "200 per 1 hour"}
```

## Examples

### Complete Workflow Example

```bash
#!/bin/bash

# 1. Submit conversion job
RESPONSE=$(curl -s -X POST http://localhost:5000/api/v1/convert \
  -F "file=@document.pdf" \
  -F "to_format=markdown" \
  -F "engine=marker")

# Extract job ID
JOB_ID=$(echo $RESPONSE | jq -r '.job_id')
echo "Job submitted: $JOB_ID"

# 2. Poll for completion
while true; do
  STATUS_RESPONSE=$(curl -s http://localhost:5000/api/v1/status/$JOB_ID)
  STATUS=$(echo $STATUS_RESPONSE | jq -r '.status')
  PROGRESS=$(echo $STATUS_RESPONSE | jq -r '.progress')

  echo "Status: $STATUS, Progress: $PROGRESS%"

  if [ "$STATUS" == "success" ]; then
    echo "Conversion completed!"
    break
  elif [ "$STATUS" == "failure" ]; then
    ERROR=$(echo $STATUS_RESPONSE | jq -r '.error')
    echo "Conversion failed: $ERROR"
    exit 1
  fi

  sleep 2
done

# 3. Download result
DOWNLOAD_URL=$(echo $STATUS_RESPONSE | jq -r '.download_url')
curl -O -J http://localhost:5000$DOWNLOAD_URL

echo "Download complete!"
```

### Python Example

```python
import requests
import time

# 1. Submit job
with open('document.pdf', 'rb') as f:
    response = requests.post('http://localhost:5000/api/v1/convert', files={
        'file': f,
    }, data={
        'to_format': 'markdown',
        'engine': 'marker',
        'force_ocr': 'true'
    })

job = response.json()
job_id = job['job_id']
print(f"Job submitted: {job_id}")

# 2. Poll for completion
while True:
    status_response = requests.get(f'http://localhost:5000/api/v1/status/{job_id}')
    status_data = status_response.json()

    status = status_data['status']
    progress = status_data['progress']
    print(f"Status: {status}, Progress: {progress}%")

    if status == 'success':
        break
    elif status == 'failure':
        print(f"Error: {status_data['error']}")
        exit(1)

    time.sleep(2)

# 3. Download result
download_url = status_data['download_url']
download_response = requests.get(f'http://localhost:5000{download_url}')

with open('result.md', 'wb') as f:
    f.write(download_response.content)

print("Download complete!")
```

### JavaScript/Node.js Example

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

async function convertDocument() {
  // 1. Submit job
  const form = new FormData();
  form.append('file', fs.createReadStream('document.pdf'));
  form.append('to_format', 'markdown');
  form.append('engine', 'marker');

  const submitResponse = await axios.post('http://localhost:5000/api/v1/convert', form, {
    headers: form.getHeaders()
  });

  const jobId = submitResponse.data.job_id;
  console.log(`Job submitted: ${jobId}`);

  // 2. Poll for completion
  while (true) {
    const statusResponse = await axios.get(`http://localhost:5000/api/v1/status/${jobId}`);
    const statusData = statusResponse.data;

    console.log(`Status: ${statusData.status}, Progress: ${statusData.progress}%`);

    if (statusData.status === 'success') {
      // 3. Download result
      const downloadUrl = statusData.download_url;
      const downloadResponse = await axios.get(`http://localhost:5000${downloadUrl}`, {
        responseType: 'arraybuffer'
      });

      fs.writeFileSync('result.md', downloadResponse.data);
      console.log('Download complete!');
      break;
    } else if (statusData.status === 'failure') {
      console.error(`Conversion failed: ${statusData.error}`);
      process.exit(1);
    }

    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

convertDocument();
```

## Best Practices

1. **Polling Interval**: Use 2-5 second intervals when polling `/api/v1/status/{job_id}` to balance responsiveness and server load.

2. **Error Handling**: Always check for `failure` status and handle errors gracefully.

3. **File Size**: Keep uploads under 100MB. Larger files will be rejected with HTTP 413.

4. **Format Validation**: Call `/api/v1/formats` to verify supported conversions before submitting jobs.

5. **Download Window**: Download files promptly after completion. Files are deleted after:
   - 10 minutes after download
   - 1 hour if not downloaded
   - 5 minutes after failure

6. **Engine Selection**:
   - Use `engine=marker` for PDF to Markdown conversions (AI-powered, high quality)
   - Use `engine=pandoc` for all other conversions (universal, fast)

7. **Multi-file Handling**: Check `is_multifile` in status response to determine if download will be a ZIP.

## Future Enhancements

Planned features for future API versions:

- **API Key Authentication**: User-specific rate limits and quotas
- **Webhook Callbacks**: Receive notifications when jobs complete
- **Batch Conversion**: Submit multiple files in a single request
- **WebSocket API**: Real-time progress updates without polling
- **Metadata Extraction**: Return extracted document metadata
- **Custom Retention**: Configure per-job file retention periods

## Support

For issues, feature requests, or questions about the API:

- **GitHub Issues**: https://github.com/yourusername/docuflux/issues
- **Documentation**: https://github.com/yourusername/docuflux/docs

## Changelog

### v1.0.0 (2026-02-01)

- Initial REST API release
- Endpoints: convert, status, download, formats
- IP-based rate limiting
- CSRF exemption for REST compatibility
- Multi-file ZIP support
- Marker AI engine support
