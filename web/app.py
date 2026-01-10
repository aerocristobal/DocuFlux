import os
import uuid
import time
import redis
import shutil
import magic
import requests
import logging
import sys
from flask import Flask, render_template, request, send_from_directory, jsonify, session
from werkzeug.utils import secure_filename
from celery import Celery
from datetime import datetime, timezone, timedelta

# Configure Structured Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}'))
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key') # Needed for session

UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'data/uploads')
OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', 'data/outputs')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 # 100MB limit

# Minimum free space required (in bytes) - 500MB
MIN_FREE_SPACE = 500 * 1024 * 1024 

@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'error': 'File too large. Maximum size is 100MB.'}), 413

@app.after_request
def add_security_headers(response):
    """Add security headers to every response."""
    # CSP: Allow Material Web (esm.run, jsdelivr), Google Fonts, and inline scripts/styles
    csp = (
        "default-src 'self' https://esm.run https://fonts.googleapis.com https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline' https://esm.run https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://esm.run https://cdn.jsdelivr.net;"
    )
    response.headers['Content-Security-Policy'] = csp
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# Metadata Redis client (DB 1) with connection pooling
redis_client = redis.Redis.from_url(
    os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1'),
    max_connections=10,
    decode_responses=True
)

# Celery configuration
celery = Celery(
    'tasks',
    broker=os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0'),
    backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
)

def check_disk_space():
    """Check if there is enough free space in the upload directory."""
    try:
        total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
        return free >= MIN_FREE_SPACE
    except Exception:
        return True # Assume space if check fails to avoid blocking

MARKER_API_URL = os.environ.get('MARKER_API_URL', 'http://marker-api:8000')

@app.route('/api/status/services')
def service_status():
    """Check status of dependent services."""
    status = {
        'marker_api': 'unknown',
        'disk_space': 'ok'
    }
    
    # Check Marker API
    try:
        # Fast timeout check
        resp = requests.get(f"{MARKER_API_URL}/health", timeout=2) 
        if resp.status_code == 200:
            status['marker_api'] = 'available'
        else:
            status['marker_api'] = 'unavailable'
    except Exception:
        status['marker_api'] = 'unavailable'
        
    # Check Disk Space
    if not check_disk_space():
        status['disk_space'] = 'low'
        
    return jsonify(status)

FORMATS = [
    {'name': 'Pandoc Markdown', 'key': 'markdown', 'direction': 'Both', 'extension': '.md', 'category': 'Markdown', 'mime_types': ['text/plain', 'text/markdown', 'text/x-markdown']},
    {'name': 'GitHub Flavored Markdown', 'key': 'gfm', 'direction': 'Both', 'extension': '.md', 'category': 'Markdown', 'mime_types': ['text/plain', 'text/markdown', 'text/x-markdown']},
    {'name': 'HTML5', 'key': 'html', 'direction': 'Both', 'extension': '.html', 'category': 'Web', 'mime_types': ['text/html']},
    {'name': 'Jupyter Notebook', 'key': 'ipynb', 'direction': 'Both', 'extension': '.ipynb', 'category': 'Web', 'mime_types': ['text/plain', 'application/json']},
    {'name': 'Microsoft Word', 'key': 'docx', 'direction': 'Both', 'extension': '.docx', 'category': 'Office', 'mime_types': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document']},
    {'name': 'Microsoft PowerPoint', 'key': 'pptx', 'direction': 'Output Only', 'extension': '.pptx', 'category': 'Office', 'mime_types': ['application/vnd.openxmlformats-officedocument.presentationml.presentation']},
    {'name': 'OpenOffice / LibreOffice', 'key': 'odt', 'direction': 'Both', 'extension': '.odt', 'category': 'Office', 'mime_types': ['application/vnd.oasis.opendocument.text']},
    {'name': 'Rich Text Format', 'key': 'rtf', 'direction': 'Both', 'extension': '.rtf', 'category': 'Office', 'mime_types': ['text/rtf']},
    {'name': 'EPUB (v3)', 'key': 'epub3', 'direction': 'Both', 'extension': '.epub', 'category': 'E-Books', 'mime_types': ['application/epub+zip']},
    {'name': 'EPUB (v2)', 'key': 'epub2', 'direction': 'Both', 'extension': '.epub', 'category': 'E-Books', 'mime_types': ['application/epub+zip']},
    {'name': 'LaTeX', 'key': 'latex', 'direction': 'Both', 'extension': '.tex', 'category': 'Technical', 'mime_types': ['text/x-tex', 'text/plain']},
    {'name': 'PDF (via LaTeX)', 'key': 'pdf', 'direction': 'Output Only', 'extension': '.pdf', 'category': 'Technical', 'mime_types': ['application/pdf']},
    {'name': 'PDF (High Accuracy)', 'key': 'pdf_marker', 'direction': 'Input Only', 'extension': '.pdf', 'category': 'Technical', 'mime_types': ['application/pdf']},
    {'name': 'AsciiDoc', 'key': 'asciidoc', 'direction': 'Both', 'extension': '.adoc', 'category': 'Technical', 'mime_types': ['text/plain']},
    {'name': 'reStructuredText', 'key': 'rst', 'direction': 'Both', 'extension': '.rst', 'category': 'Technical', 'mime_types': ['text/plain', 'text/x-rst']},
    {'name': 'BibTeX (Bibliography)', 'key': 'bibtex', 'direction': 'Both', 'extension': '.bib', 'category': 'Technical', 'mime_types': ['text/plain', 'text/x-bibtex']},
    {'name': 'MediaWiki', 'key': 'mediawiki', 'direction': 'Both', 'extension': '.wiki', 'category': 'Wiki', 'mime_types': ['text/plain']},
    {'name': 'Jira Wiki', 'key': 'jira', 'direction': 'Both', 'extension': '.txt', 'category': 'Wiki', 'mime_types': ['text/plain']},
]

def update_job_metadata(job_id, updates):
    """Update job metadata using Redis Hash (atomic operation)."""
    key = f"job:{job_id}"
    try:
        redis_client.hset(key, mapping=updates)
    except Exception as e:
        logging.error(f"Error updating metadata for {job_id}: {e}")

@app.route('/')
def index():
    return render_template('index.html', formats=FORMATS)

@app.route('/convert', methods=['POST'])
def convert():
    # Pre-check disk space
    if not check_disk_space():
        return jsonify({'error': 'Server storage is full. Please try again later.'}), 507 # Insufficient Storage

    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    from_format = request.form.get('from_format')
    to_format = request.form.get('to_format')
    
    if not from_format or not to_format:
        return jsonify({'error': "Missing format selection"}), 400

    # Find format info
    from_info = next((f for f in FORMATS if f['key'] == from_format), None)
    to_info = next((f for f in FORMATS if f['key'] == to_format), None)

    if not from_info or not to_info:
        return jsonify({'error': "Invalid format selection"}), 400

    if from_info['direction'] == 'Output Only':
        return jsonify({'error': f"{from_info['name']} is only available as an output format"}), 400

    # Validate file extension matches selection
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext != from_info['extension']:
        if not (from_info['key'] == 'markdown' and file_ext in ['.md', '.markdown']):
             return jsonify({'error': f"File extension {file_ext} does not match selected format {from_info['name']}. Expected: {from_info['extension']}"}), 400

    # MIME type validation
    allowed_mimes = from_info.get('mime_types', [])
    if allowed_mimes:
        try:
            # Read header to detect mime type
            header = file.read(2048)
            mime = magic.Magic(mime=True)
            file_mime = mime.from_buffer(header)
            file.seek(0) # Reset stream position

            # Special handling for text files which might just be "text/plain" or "text/x-..."
            is_valid = False
            if file_mime in allowed_mimes:
                is_valid = True
            elif 'text/plain' in allowed_mimes and file_mime.startswith('text/'):
                # Allow generic text if text/plain is allowed
                is_valid = True
            
            if not is_valid:
                logging.warning(f"MIME check failed. Detected: {file_mime}, Allowed: {allowed_mimes}")
                return jsonify({'error': f"Invalid file content. Detected: {file_mime}. Please ensure the file matches the selected format."}), 400
                
        except Exception as e:
            logging.error(f"MIME detection error: {e}")
            # If magic fails, fall back to extension check (which passed) or block?
            # Safe to block if we want strict security.
            return jsonify({'error': "Could not validate file type."}), 500

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(app.config['UPLOAD_FOLDER'], job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    input_filename = secure_filename(file.filename)
    if not input_filename:
        # Fallback for filenames that secure_filename might strip completely
        input_filename = f"file_{job_id}"
        
    input_path = os.path.join(job_dir, input_filename)
    file.save(input_path)
    
    # Create output directory
    output_job_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_id)
    os.makedirs(output_job_dir, exist_ok=True)
    
    # Determine output filename
    base_name = os.path.splitext(input_filename)[0]
    ext = to_info['extension'].lstrip('.')
    output_filename = f"{base_name}.{ext}"
    output_path = os.path.join(output_job_dir, output_filename)

    # Initialize Metadata
    update_job_metadata(job_id, {
        'status': 'PENDING',
        'created_at': str(time.time()),
        'filename': input_filename
    })

    # Queue the task
    task_name = 'tasks.convert_with_marker' if from_format == 'pdf_marker' else 'tasks.convert_document'
    task = celery.send_task(task_name, args=[
        job_id, input_filename, output_filename, from_format, to_format
    ], task_id=job_id)
    
    # Store job in session
    if 'jobs' not in session:
        session['jobs'] = []
    
    session['jobs'].append({
        'id': job_id,
        'filename': input_filename,
        'from': from_format,
        'to': to_format,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'input_path': input_path, # Stored for retry
        'output_path': output_path # Stored for retry
    })
    session.modified = True

    return jsonify({'job_id': job_id, 'status': 'queued'})

@app.route('/api/jobs')
def list_jobs():
    if 'jobs' not in session or not session['jobs']:
        return jsonify([])

    # Filter out jobs older than 60 minutes
    threshold = datetime.now(timezone.utc) - timedelta(minutes=60)
    valid_jobs = []
    
    for job in session['jobs']:
        try:
            # Handle potential legacy naive timestamps by assuming UTC if tzinfo is missing
            job_time = datetime.fromisoformat(job['created_at'])
            if job_time.tzinfo is None:
                job_time = job_time.replace(tzinfo=timezone.utc)
            
            if job_time > threshold:
                valid_jobs.append(job)
        except Exception:
            # If date parsing fails, remove the job
            pass
            
    session['jobs'] = valid_jobs
    session.modified = True
    
    if not session['jobs']:
        return jsonify([])

    # Batch fetch all job metadata using Redis pipeline (fixes N+1 query)
    pipe = redis_client.pipeline()
    for job in session['jobs']:
        pipe.hgetall(f"job:{job['id']}")
    results = pipe.execute()

    jobs_data = []
    for job, meta in zip(session['jobs'], results):
        # Get status from Redis metadata, fallback to Celery if needed
        status = meta.get('status', 'PENDING') if meta else 'PENDING'
        error = meta.get('error') if meta else None

        jobs_data.append({
            'id': job['id'],
            'filename': job['filename'],
            'from': job['from'],
            'to': job['to'],
            'created_at': job['created_at'],
            'status': status,
            'result': error if status == 'FAILURE' else None,
            'download_url': f"/download/{job['id']}" if status == 'SUCCESS' else None
        })

    # Sort by newest first
    jobs_data.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(jobs_data)

def is_valid_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

@app.route('/api/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    if not is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400
    celery.control.revoke(job_id, terminate=True)
    
    # Update status to REVOKED in Redis
    update_job_metadata(job_id, {'status': 'REVOKED'})
    
    return jsonify({'status': 'cancelled'})

@app.route('/api/delete/<job_id>', methods=['POST'])
def delete_job(job_id):
    if not is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400
    if 'jobs' not in session:
        return jsonify({'error': 'Job not found'}), 404
        
    # Remove from session
    session['jobs'] = [j for j in session['jobs'] if j['id'] != job_id]
    session.modified = True
    
    # Clean up files immediately (optional, or let background task handle it)
    # Ideally we remove files to free space
    job_dir = os.path.join(app.config['UPLOAD_FOLDER'], job_id)
    output_job_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_id)
    
    try:
        if os.path.exists(job_dir):
            shutil.rmtree(job_dir)
        if os.path.exists(output_job_dir):
            shutil.rmtree(output_job_dir)
    except Exception as e:
        logging.error(f"Error deleting files for {job_id}: {e}")

    # Remove metadata
    redis_client.delete(f"job:{job_id}")
    
    return jsonify({'status': 'deleted'})

@app.route('/api/retry/<job_id>', methods=['POST'])
def retry_job(job_id):
    if not is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job ID'}), 400
    if 'jobs' not in session:
        return jsonify({'error': 'Job not found'}), 404
    
    job_data = next((j for j in session['jobs'] if j['id'] == job_id), None)
    if not job_data:
        return jsonify({'error': 'Job not found'}), 404

    # Check if original input file still exists
    old_input_path = job_data['input_path']
    if not os.path.exists(old_input_path):
        return jsonify({'error': 'Original input file has been cleaned up and cannot be retried.'}), 400

    # New job ID for the retry
    new_job_id = str(uuid.uuid4())
    
    # Setup new directories
    new_job_dir = os.path.join(app.config['UPLOAD_FOLDER'], new_job_id)
    os.makedirs(new_job_dir, exist_ok=True)
    
    new_output_job_dir = os.path.join(app.config['OUTPUT_FOLDER'], new_job_id)
    os.makedirs(new_output_job_dir, exist_ok=True)
    
    # Copy input file
    input_filename = os.path.basename(old_input_path)
    new_input_path = os.path.join(new_job_dir, input_filename)
    try:
        shutil.copy2(old_input_path, new_input_path)
    except IOError:
        return jsonify({'error': 'Failed to copy input file for retry.'}), 500
    
    # Determine output path
    output_filename = os.path.basename(job_data['output_path'])
    new_output_path = os.path.join(new_output_job_dir, output_filename)

    # Initialize Metadata for new job
    update_job_metadata(new_job_id, {
        'status': 'PENDING',
        'created_at': str(time.time()),
        'filename': input_filename,
        'is_retry': 'true',
        'original_job_id': job_id
    })
    
    # Queue task with NEW paths
    task_name = 'tasks.convert_with_marker' if job_data['from'] == 'pdf_marker' else 'tasks.convert_document'
    celery.send_task(task_name, args=[
        new_job_id, input_filename, output_filename, job_data['from'], job_data['to']
    ], task_id=new_job_id)
    
    # Add new job to session
    session['jobs'].append({
        'id': new_job_id,
        'filename': job_data['filename'],
        'from': job_data['from'],
        'to': job_data['to'],
        'created_at': datetime.now(timezone.utc).isoformat(),
        'input_path': new_input_path,
        'output_path': new_output_path
    })
    session.modified = True
    
    return jsonify({'status': 'retried', 'new_job_id': new_job_id})

@app.route('/download/<job_id>')
def download_file(job_id):
    if not is_valid_uuid(job_id):
        return "Invalid job ID", 400
    job_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_id)
    
    # Security/Existence check
    if not os.path.exists(job_dir):
        return "File not found (may have been cleaned up)", 404
        
    try:
        files = os.listdir(job_dir)
    except OSError:
        return "File not found", 404

    if not files:
        return "File not found", 404
    
    # Mark as downloaded (Ephemeral Data Handling)
    update_job_metadata(job_id, {'downloaded_at': str(time.time())})
    
    return send_from_directory(job_dir, files[0], as_attachment=True)

if __name__ == '__main__':
    # Default to False for security. Enable only in development.
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
