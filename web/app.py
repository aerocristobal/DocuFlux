from gevent import monkey
monkey.patch_all()

import os
import uuid
import time
import redis
import shutil
import magic
import requests
import logging
import sys
import zipfile
import io
from flask import Flask, render_template, request, send_from_directory, jsonify, session, send_file
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from celery import Celery
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_socketio import SocketIO
from datetime import datetime, timezone, timedelta

# Epic 21.7: Import secrets management
from secrets import validate_secrets_at_startup, load_secret

# Epic 23.3: Import encryption modules
from encryption import EncryptionService
from key_manager import create_key_manager
import tempfile

# Configure Structured Logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}'))
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

# Epic 21.7: Validate secrets at startup
try:
    app_secrets = validate_secrets_at_startup()
except ValueError as e:
    logging.error(f"Failed to load secrets: {e}")
    sys.exit(1)

app = Flask(__name__)
# Epic 21.7: Use validated secret from secrets module
app.secret_key = app_secrets['SECRET_KEY']

# Epic 22.4: ProxyFix middleware for Cloudflare Tunnel / reverse proxy support
# Trust proxy headers (X-Forwarded-For, X-Forwarded-Proto, etc.)
if os.environ.get('BEHIND_PROXY', 'false').lower() == 'true':
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,      # Trust 1 proxy for X-Forwarded-For
        x_proto=1,    # Trust 1 proxy for X-Forwarded-Proto (http/https)
        x_host=1,     # Trust 1 proxy for X-Forwarded-Host
        x_prefix=1    # Trust 1 proxy for X-Forwarded-Prefix
    )
    logging.info("ProxyFix middleware enabled - trusting proxy headers")

# Security Hardening for Cookies
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
)

@app.before_request
def ensure_session_id():
    session.permanent = True
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        logging.info(f"New session created: {session['session_id']}")

# Rate Limiting Configuration
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri=os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1'),
    strategy="fixed-window",
)

# CSRF Protection
csrf = CSRFProtect(app)

# WebSocket Initialization
socketio = SocketIO(
    app, 
    message_queue=os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1'),
    cors_allowed_origins="*"
)

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
    # Epic 22.3: Updated CSP to support both ws:// and wss:// WebSocket connections
    # When behind proxy (HTTPS), Socket.IO auto-upgrades to wss://
    csp = (
        "default-src 'self' https://esm.run https://fonts.googleapis.com https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline' https://esm.run https://cdn.jsdelivr.net https://cdn.socket.io; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://esm.run https://cdn.jsdelivr.net ws: wss:;"
    )
    response.headers['Content-Security-Policy'] = csp
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # Epic 22.4: Enable HSTS when running behind HTTPS proxy
    behind_proxy = os.environ.get('BEHIND_PROXY', 'false').lower() == 'true'
    if behind_proxy or not app.debug:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    return response

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded", "message": str(e.description)}), 429

# Epic 24.1: Redis TLS Configuration
# Metadata Redis client (DB 1) with connection pooling optimization and TLS support
redis_url = os.environ.get('REDIS_METADATA_URL', 'redis://redis:6379/1')

# Configure TLS parameters if using rediss://
redis_kwargs = {
    'max_connections': 50,
    'decode_responses': True
}

if redis_url.startswith('rediss://'):
    # Enable TLS
    redis_kwargs['ssl'] = True
    redis_kwargs['ssl_cert_reqs'] = 'required'

    # Certificate paths from environment
    ca_certs = os.environ.get('REDIS_TLS_CA_CERTS')
    certfile = os.environ.get('REDIS_TLS_CERTFILE')
    keyfile = os.environ.get('REDIS_TLS_KEYFILE')

    if ca_certs:
        redis_kwargs['ssl_ca_certs'] = ca_certs
    if certfile:
        redis_kwargs['ssl_certfile'] = certfile
    if keyfile:
        redis_kwargs['ssl_keyfile'] = keyfile

    logging.info(f"Redis TLS enabled with CA: {ca_certs}")

redis_client = redis.Redis.from_url(redis_url, **redis_kwargs)

# Celery configuration
celery = Celery(
    'tasks',
    broker=os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0'),
    backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
)
celery.conf.task_routes = {
    'tasks.convert_document': {'queue': 'default'},
    'tasks.convert_with_marker': {'queue': 'default'},
}

# Epic 24.2: Celery Task Message Encryption
# Enable message signing for task integrity and authentication
celery_signing_key = app_secrets.get('CELERY_SIGNING_KEY')
if celery_signing_key:
    celery.conf.task_serializer = 'auth'
    celery.conf.result_serializer = 'json'
    celery.conf.accept_content = ['auth', 'application/json']
    celery.conf.security_key = celery_signing_key
    celery.conf.security_certificate = None  # Using symmetric key, not certificates
    celery.conf.security_digest = 'sha256'
    logging.info("Celery message signing enabled (task_serializer=auth)")
else:
    logging.warning("Celery signing key not set - messages not authenticated")

# Epic 23.3: Initialize encryption components (lazily)
_encryption_service = None
_key_manager = None

def get_encryption_service():
    """Get or create encryption service instance."""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service

def get_key_manager():
    """Get or create key manager instance."""
    global _key_manager
    if _key_manager is None:
        _key_manager = create_key_manager(redis_client)
    return _key_manager

def decrypt_file_to_temp(encrypted_path, job_id):
    """
    Decrypt an encrypted file to a temporary location.

    Epic 23.3: Transparent decryption on download

    Args:
        encrypted_path: Path to encrypted file
        job_id: Job identifier for key retrieval

    Returns:
        Path to decrypted temporary file, or None if decryption failed
    """
    try:
        encryption_service = get_encryption_service()
        key_manager = get_key_manager()

        # Get DEK for this job
        dek = key_manager.get_job_key(job_id)
        if dek is None:
            logging.error(f"No decryption key found for job {job_id}")
            return None

        # Create temporary file for decrypted content
        temp_fd, temp_path = tempfile.mkstemp(suffix=os.path.splitext(encrypted_path)[1])
        os.close(temp_fd)

        # Decrypt file
        encryption_service.decrypt_file(
            input_path=encrypted_path,
            output_path=temp_path,
            key=dek,
            associated_data=job_id
        )

        logging.info(f"Decrypted file for download: {encrypted_path} -> {temp_path}")
        return temp_path

    except Exception as e:
        logging.error(f"Decryption failed for {encrypted_path}: {e}")
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.remove(temp_path)
        return None

def check_disk_space():
    try:
        total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
        return free >= MIN_FREE_SPACE
    except Exception:
        return True

@app.route('/api/status/services')
def service_status():
    status = {'disk_space': 'ok'}
    if not check_disk_space():
        status['disk_space'] = 'low'

    # Check Marker status from Redis
    try:
        marker_status = redis_client.get("service:marker:status") or "initializing"
        marker_eta = redis_client.get("service:marker:eta") or "calculating..."

        status['marker'] = marker_status
        status['marker_status'] = marker_status  # Alias for consistency
        status['llm_download_eta'] = marker_eta
        status['models_cached'] = (marker_status == 'ready')
    except Exception as e:
        logging.error(f"Error checking marker status: {e}")
        status['marker'] = 'error'
        status['marker_status'] = 'error'

    # Get GPU status and info from Redis
    try:
        gpu_status = redis_client.get("marker:gpu_status") or "initializing"
        status['gpu_status'] = gpu_status

        # Get detailed GPU info
        gpu_info_raw = redis_client.hgetall("marker:gpu_info")
        if gpu_info_raw:
            # Convert byte keys/values to strings and parse numbers
            gpu_info = {}
            for key, value in gpu_info_raw.items():
                # Decode if bytes
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                if isinstance(value, bytes):
                    value = value.decode('utf-8')

                # Try to convert numeric strings to numbers
                try:
                    if '.' in value:
                        gpu_info[key] = float(value)
                    elif value.isdigit():
                        gpu_info[key] = int(value)
                    else:
                        gpu_info[key] = value
                except (ValueError, AttributeError):
                    gpu_info[key] = value

            status['gpu_info'] = gpu_info
        else:
            status['gpu_info'] = {"status": "initializing"}

    except Exception as e:
        logging.error(f"Error checking GPU status: {e}")
        status['gpu_status'] = 'unavailable'
        status['gpu_info'] = {"status": "unavailable", "error": str(e)}

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
    key = f"job:{job_id}"
    try:
        redis_client.hset(key, mapping=updates)
        full_meta = redis_client.hgetall(key)
        full_meta['id'] = job_id
        socketio.emit('job_update', full_meta, namespace='/')
    except Exception as e:
        logging.error(f"Error updating metadata for {job_id}: {e}")

@app.route('/')
def index():
    return render_template('index.html', formats=FORMATS)

@app.route('/convert', methods=['POST'])
def convert():
    if not check_disk_space():
        return jsonify({'error': 'Server storage is full.'}), 507
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    files = request.files.getlist('file')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No selected file'}), 400
    from_format = request.form.get('from_format')
    to_format = request.form.get('to_format')
    if not from_format or not to_format:
        return jsonify({'error': "Missing format selection"}), 400
    from_info = next((f for f in FORMATS if f['key'] == from_format), None)
    to_info = next((f for f in FORMATS if f['key'] == to_format), None)
    if not from_info or not to_info:
        return jsonify({'error': "Invalid format selection"}), 400
    
    session_id = session.get('session_id')
    job_ids = []
    for file in files:
        if file.filename == '': continue
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != from_info['extension']:
            if not (from_info['key'] == 'markdown' and file_ext in ['.md', '.markdown']):
                 return jsonify({'error': f"Extension {file_ext} mismatch."}), 400
        
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(app.config['UPLOAD_FOLDER'], job_id)
        os.makedirs(job_dir, exist_ok=True)
        input_filename = secure_filename(file.filename) or f"file_{job_id}"
        input_path = os.path.join(job_dir, input_filename)
        file.save(input_path)
        
        output_job_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_id)
        os.makedirs(output_job_dir, exist_ok=True)
        base_name = os.path.splitext(input_filename)[0]
        output_filename = f"{base_name}.{to_info['extension'].lstrip('.')}"
        
        update_job_metadata(job_id, {
            'status': 'PENDING', 'created_at': str(time.time()), 'filename': file.filename,
            'from': from_format, 'to': to_format,
            'force_ocr': str(request.form.get('force_ocr') == 'on'),
            'use_llm': str(request.form.get('use_llm') == 'on')
        })
        
        file_size = os.path.getsize(input_path)
        target_queue = 'high_priority' if file_size < 5 * 1024 * 1024 else 'default'
        
        task_name = 'tasks.convert_with_marker' if from_format == 'pdf_marker' else 'tasks.convert_document'
        task_args = [job_id, input_filename, output_filename, from_format, to_format]
        
        if from_format == 'pdf_marker':
            options = {
                'force_ocr': request.form.get('force_ocr') == 'on',
                'use_llm': request.form.get('use_llm') == 'on'
            }
            task_args.append(options)

        celery.send_task(task_name, args=task_args, task_id=job_id, queue=target_queue)
        
        history_key = f"history:{session_id}"
        redis_client.lpush(history_key, job_id)
        redis_client.expire(history_key, 86400) # 24 hours
        job_ids.append(job_id)

    return jsonify({'job_ids': job_ids, 'status': 'queued'})

@app.route('/api/jobs')
def list_jobs():
    session_id = session.get('session_id')
    if not session_id: return jsonify([])
    history_key = f"history:{session_id}"
    job_ids = redis_client.lrange(history_key, 0, -1)
    if not job_ids: return jsonify([])
    
    pipe = redis_client.pipeline()
    for jid in job_ids: pipe.hgetall(f"job:{jid}")
    results = pipe.execute()

    jobs_data = []
    for jid, meta in zip(job_ids, results):
        if not meta: continue
        
        # Check output files for download type
        job_dir = os.path.join(OUTPUT_FOLDER, jid)
        file_count = 0
        if meta.get('status') == 'SUCCESS':
            if os.path.exists(job_dir):
                for root, dirs, files in os.walk(job_dir):
                    file_count += len(files)
                logging.info(f"Job {jid}: Found {file_count} files. is_zip={file_count > 1}")
            else:
                logging.warning(f"Job {jid}: SUCCESS but dir {job_dir} missing")
        
        is_zip = file_count > 1
        download_url = None
        if meta.get('status') == 'SUCCESS':
            download_url = f"/download_zip/{jid}" if is_zip else f"/download/{jid}"

        jobs_data.append({
            'id': jid, 'filename': meta.get('filename'), 'from': meta.get('from'),
            'to': meta.get('to'), 'created_at': float(meta.get('created_at', 0)),
            'status': meta.get('status', 'PENDING'), 'progress': meta.get('progress', '0'),
            'result': meta.get('error') if meta.get('status') == 'FAILURE' else None,
            'download_url': download_url,
            'is_zip': is_zip,
            'file_count': file_count
        })
    jobs_data.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(jobs_data)

def is_valid_uuid(val):
    try: uuid.UUID(str(val)); return True
    except ValueError: return False

@app.route('/api/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    if not is_valid_uuid(job_id): return jsonify({'error': 'Invalid ID'}), 400
    celery.control.revoke(job_id, terminate=True)
    update_job_metadata(job_id, {'status': 'REVOKED', 'progress': '0'})
    return jsonify({'status': 'cancelled'})

@app.route('/api/delete/<job_id>', methods=['POST'])
def delete_job(job_id):
    if not is_valid_uuid(job_id): return jsonify({'error': 'Invalid ID'}), 400
    safe_job_id = secure_filename(job_id)
    session_id = session.get('session_id')
    if session_id: redis_client.lrem(f"history:{session_id}", 0, job_id)
    
    for base in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
        p = os.path.join(base, safe_job_id)
        if os.path.exists(p): shutil.rmtree(p)
    redis_client.delete(f"job:{job_id}")
    return jsonify({'status': 'deleted'})

@app.route('/api/retry/<job_id>', methods=['POST'])
def retry_job(job_id):
    if not is_valid_uuid(job_id): return jsonify({'error': 'Invalid ID'}), 400
    safe_job_id = secure_filename(job_id)
    job_data = redis_client.hgetall(f"job:{job_id}")
    if not job_data: return jsonify({'error': 'Not found'}), 404
    input_filename = job_data.get('filename')
    old_input_path = os.path.join(UPLOAD_FOLDER, safe_job_id, input_filename)
    if not os.path.exists(old_input_path): return jsonify({'error': 'Cleaned up'}), 400

    new_job_id = str(uuid.uuid4())
    new_job_dir = os.path.join(UPLOAD_FOLDER, new_job_id)
    os.makedirs(new_job_dir, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_FOLDER, new_job_id), exist_ok=True)
    shutil.copy2(old_input_path, os.path.join(new_job_dir, input_filename))
    
    to_info = next((f for f in FORMATS if f['key'] == job_data.get('to')), None)
    output_filename = f"{os.path.splitext(input_filename)[0]}.{to_info['extension'].lstrip('.')}"

    update_job_metadata(new_job_id, {
        'status': 'PENDING', 'created_at': str(time.time()), 'filename': input_filename,
        'from': job_data.get('from'), 'to': job_data.get('to'),
        'force_ocr': job_data.get('force_ocr'),
        'use_llm': job_data.get('use_llm')
    })

    task_name = 'tasks.convert_with_marker' if job_data.get('from') == 'pdf_marker' else 'tasks.convert_document'
    task_args = [new_job_id, input_filename, output_filename, job_data.get('from'), job_data.get('to')]

    if job_data.get('from') == 'pdf_marker':
        options = {
            'force_ocr': job_data.get('force_ocr') == 'True',
            'use_llm': job_data.get('use_llm') == 'True'
        }
        task_args.append(options)

    celery.send_task(task_name, args=task_args, task_id=new_job_id)
    
    session_id = session.get('session_id')
    redis_client.lpush(f"history:{session_id}", new_job_id)
    return jsonify({'status': 'retried', 'new_job_id': new_job_id})

@app.route('/download/<job_id>')
def download_file(job_id):
    if not is_valid_uuid(job_id): return "Invalid", 400
    safe_job_id = secure_filename(job_id)
    job_dir = os.path.join(OUTPUT_FOLDER, safe_job_id)
    if not os.path.exists(job_dir): return "Not found", 404
    files = [f for f in os.listdir(job_dir) if os.path.isfile(os.path.join(job_dir, f)) and not f.startswith('.')]
    if not files: return "Not found", 404
    # Prefer non-json/metadata files if possible, or just take first
    target_file = files[0]
    # Filter out metadata.json if there are other files
    if len(files) > 1:
        others = [f for f in files if f != 'metadata.json']
        if others: target_file = others[0]

    # Epic 23.3: Decrypt file transparently before sending
    encrypted_path = os.path.join(job_dir, target_file)

    # Check if file is encrypted (has encrypted flag in metadata)
    job_meta = redis_client.hgetall(f"job:{job_id}")
    is_encrypted = job_meta.get('encrypted') == 'true'

    if is_encrypted:
        # Decrypt file to temporary location
        decrypted_path = decrypt_file_to_temp(encrypted_path, job_id)
        if decrypted_path is None:
            return "Decryption failed", 500

        # Epic 21.6: Track both downloaded_at (first download) and last_viewed (latest access)
        current_time = str(time.time())
        update_job_metadata(job_id, {
            'downloaded_at': str(time.time()),
            'last_viewed': current_time
        })

        # Send decrypted file and clean up temp file after sending
        try:
            return send_file(
                decrypted_path,
                as_attachment=True,
                download_name=target_file
            )
        finally:
            # Clean up temporary decrypted file
            if os.path.exists(decrypted_path):
                os.remove(decrypted_path)
    else:
        # File not encrypted, send directly
        # Epic 21.6: Track both downloaded_at (first download) and last_viewed (latest access)
        current_time = str(time.time())
        update_job_metadata(job_id, {
            'downloaded_at': str(time.time()),
            'last_viewed': current_time
        })
        return send_from_directory(job_dir, target_file, as_attachment=True)

@app.route('/download_zip/<job_id>')
def download_zip(job_id):
    if not is_valid_uuid(job_id): return "Invalid", 400
    safe_job_id = secure_filename(job_id)
    job_dir = os.path.join(OUTPUT_FOLDER, safe_job_id)
    if not os.path.exists(job_dir): return "Not found", 404

    # Epic 23.3: Check if files are encrypted
    job_meta = redis_client.hgetall(f"job:{job_id}")
    is_encrypted = job_meta.get('encrypted') == 'true'

    memory_file = io.BytesIO()
    temp_files = []  # Track temporary decrypted files for cleanup

    try:
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(job_dir):
                for file in files:
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, job_dir)

                    if is_encrypted:
                        # Decrypt file to temporary location
                        decrypted_path = decrypt_file_to_temp(abs_path, job_id)
                        if decrypted_path is None:
                            logging.error(f"Failed to decrypt {abs_path} for zip download")
                            continue

                        # Add decrypted file to zip with original relative path
                        zf.write(decrypted_path, rel_path)
                        temp_files.append(decrypted_path)
                    else:
                        # Add file directly to zip
                        zf.write(abs_path, rel_path)

        memory_file.seek(0)

        # Epic 21.6: Track both downloaded_at (first download) and last_viewed (latest access)
        current_time = str(time.time())
        update_job_metadata(job_id, {
            'downloaded_at': str(time.time()),
            'last_viewed': current_time
        })

        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"conversion_{job_id}.zip"
        )

    finally:
        # Clean up temporary decrypted files
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)


# Epic 21.10: Enhanced Health Check Endpoints

@app.route('/healthz')
def healthz():
    """
    Liveness probe - is the process alive?

    Returns 200 if process is running, 500 if deadlocked or unresponsive.
    """
    return 'OK', 200


@app.route('/readyz')
def readyz():
    """
    Readiness probe - is the service ready to accept traffic?

    Checks:
    - Redis connectivity
    - Critical services available

    Returns:
        200 if ready, 503 if not ready
    """
    try:
        # Check Redis connectivity
        redis_client.ping()

        return jsonify({
            'status': 'ready',
            'redis': 'connected',
            'timestamp': time.time()
        }), 200

    except Exception as e:
        logging.error(f"Readiness check failed: {e}")
        return jsonify({
            'status': 'not_ready',
            'error': 'Could not connect to Redis',
            'timestamp': time.time()
        }), 503


@app.route('/api/health')
def health_detailed():
    """
    Detailed health check with component status.

    Epic 21.10: Comprehensive health status

    Returns:
        JSON with detailed component health information
    """
    health_status = {
        'status': 'healthy',
        'timestamp': time.time(),
        'components': {}
    }

    # Check Redis connectivity
    try:
        redis_client.ping()
        health_status['components']['redis'] = {
            'status': 'up',
            'response_time_ms': 'OK'
        }
    except Exception as e:
        logging.error(f"Health check failed for Redis: {e}")
        health_status['status'] = 'unhealthy'
        health_status['components']['redis'] = {
            'status': 'down',
            'error': 'Could not connect to Redis'
        }

    # Check disk space
    try:
        total, used, free = shutil.disk_usage('/app/data')
        used_percent = (used / total) * 100
        health_status['components']['disk'] = {
            'status': 'ok' if used_percent < 90 else 'warning',
            'total_gb': round(total / (1024**3), 2),
            'used_gb': round(used / (1024**3), 2),
            'free_gb': round(free / (1024**3), 2),
            'used_percent': round(used_percent, 1)
        }

        if used_percent >= 95:
            health_status['components']['disk']['status'] = 'critical'
            health_status['status'] = 'degraded'

    except Exception as e:
        logging.error(f"Health check failed for disk space: {e}")
        health_status['components']['disk'] = {
            'status': 'unknown',
            'error': 'Could not read disk space'
        }

    # Check GPU status (from worker via Redis)
    try:
        gpu_status = redis_client.get('marker:gpu_status')
        gpu_info = redis_client.hgetall('marker:gpu_info')

        health_status['components']['gpu'] = {
            'status': gpu_status or 'unknown',
            'info': gpu_info if gpu_info else {}
        }
    except Exception as e:
        logging.error(f"Health check failed for GPU status: {e}")
        health_status['components']['gpu'] = {
            'status': 'unknown',
            'error': 'Could not query GPU status from Redis'
        }

    # Check Celery worker availability
    try:
        # Check if workers are available
        inspect = celery.control.inspect()
        active_workers = inspect.active()

        if active_workers and len(active_workers) > 0:
            health_status['components']['celery_workers'] = {
                'status': 'up',
                'worker_count': len(active_workers)
            }
        else:
            health_status['components']['celery_workers'] = {
                'status': 'down',
                'worker_count': 0
            }
            health_status['status'] = 'degraded'

    except Exception as e:
        logging.error(f"Health check failed for Celery workers: {e}")
        health_status['components']['celery_workers'] = {
            'status': 'unknown',
            'error': 'Could not inspect Celery workers'
        }

    # Overall status code
    status_code = 200
    if health_status['status'] == 'degraded':
        status_code = 200  # Still operational but degraded
    elif health_status['status'] == 'unhealthy':
        status_code = 503

    return jsonify(health_status), status_code


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)