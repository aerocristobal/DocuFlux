import os
import uuid
import time
import redis
import shutil
from flask import Flask, render_template, request, send_from_directory, jsonify, session
from celery import Celery
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key') # Needed for session

UPLOAD_FOLDER = '/app/data/uploads'
OUTPUT_FOLDER = '/app/data/outputs'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

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

FORMATS = [
    {'name': 'Pandoc Markdown', 'key': 'markdown', 'direction': 'Both', 'extension': '.md', 'category': 'Markdown'},
    {'name': 'GitHub Flavored Markdown', 'key': 'gfm', 'direction': 'Both', 'extension': '.md', 'category': 'Markdown'},
    {'name': 'HTML5', 'key': 'html', 'direction': 'Both', 'extension': '.html', 'category': 'Web'},
    {'name': 'Jupyter Notebook', 'key': 'ipynb', 'direction': 'Both', 'extension': '.ipynb', 'category': 'Web'},
    {'name': 'Microsoft Word', 'key': 'docx', 'direction': 'Both', 'extension': '.docx', 'category': 'Office'},
    {'name': 'Microsoft PowerPoint', 'key': 'pptx', 'direction': 'Output Only', 'extension': '.pptx', 'category': 'Office'},
    {'name': 'OpenOffice / LibreOffice', 'key': 'odt', 'direction': 'Both', 'extension': '.odt', 'category': 'Office'},
    {'name': 'Rich Text Format', 'key': 'rtf', 'direction': 'Both', 'extension': '.rtf', 'category': 'Office'},
    {'name': 'EPUB (v3)', 'key': 'epub3', 'direction': 'Both', 'extension': '.epub', 'category': 'E-Books'},
    {'name': 'EPUB (v2)', 'key': 'epub2', 'direction': 'Both', 'extension': '.epub', 'category': 'E-Books'},
    {'name': 'LaTeX', 'key': 'latex', 'direction': 'Both', 'extension': '.tex', 'category': 'Technical'},
    {'name': 'PDF (via LaTeX)', 'key': 'pdf', 'direction': 'Output Only', 'extension': '.pdf', 'category': 'Technical'},
    {'name': 'PDF (High Accuracy)', 'key': 'pdf_marker', 'direction': 'Input Only', 'extension': '.pdf', 'category': 'Technical'},
    {'name': 'AsciiDoc', 'key': 'asciidoc', 'direction': 'Both', 'extension': '.adoc', 'category': 'Technical'},
    {'name': 'reStructuredText', 'key': 'rst', 'direction': 'Both', 'extension': '.rst', 'category': 'Technical'},
    {'name': 'BibTeX (Bibliography)', 'key': 'bibtex', 'direction': 'Both', 'extension': '.bib', 'category': 'Technical'},
    {'name': 'MediaWiki', 'key': 'mediawiki', 'direction': 'Both', 'extension': '.wiki', 'category': 'Wiki'},
    {'name': 'Jira Wiki', 'key': 'jira', 'direction': 'Both', 'extension': '.txt', 'category': 'Wiki'},
]

def update_job_metadata(job_id, updates):
    """Update job metadata using Redis Hash (atomic operation)."""
    key = f"job:{job_id}"
    try:
        redis_client.hset(key, mapping=updates)
    except Exception as e:
        print(f"Error updating metadata for {job_id}: {e}")

@app.route('/')
def index():
    return render_template('index.html', formats=FORMATS)

@app.route('/convert', methods=['POST'])
def convert():
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

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(app.config['UPLOAD_FOLDER'], job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    input_filename = file.filename
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
        job_id, input_path, output_path, from_format, to_format
    ], task_id=job_id)
    
    # Store job in session
    if 'jobs' not in session:
        session['jobs'] = []
    
    session['jobs'].append({
        'id': job_id,
        'filename': input_filename,
        'from': from_format,
        'to': to_format,
        'created_at': datetime.now().isoformat(),
        'input_path': input_path, # Stored for retry
        'output_path': output_path # Stored for retry
    })
    session.modified = True

    return jsonify({'job_id': job_id, 'status': 'queued'})

@app.route('/api/jobs')
def list_jobs():
    if 'jobs' not in session or not session['jobs']:
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

@app.route('/api/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    celery.control.revoke(job_id, terminate=True)
    
    # Update local session to reflect cancelled? 
    # Actually celery status might stay PENDING or go to REVOKED depending on timing.
    # We can remove it from session or just let the status update handle it.
    # Requirement: "Queued job is removed from the list".
    
    if 'jobs' in session:
        session['jobs'] = [j for j in session['jobs'] if j['id'] != job_id]
        session.modified = True
        
    return jsonify({'status': 'cancelled'})

@app.route('/api/retry/<job_id>', methods=['POST'])
def retry_job(job_id):
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
        new_job_id, new_input_path, new_output_path, job_data['from'], job_data['to']
    ], task_id=new_job_id)
    
    # Add new job to session
    session['jobs'].append({
        'id': new_job_id,
        'filename': job_data['filename'],
        'from': job_data['from'],
        'to': job_data['to'],
        'created_at': datetime.now().isoformat(),
        'input_path': new_input_path,
        'output_path': new_output_path
    })
    session.modified = True
    
    return jsonify({'status': 'retried', 'new_job_id': new_job_id})

@app.route('/download/<job_id>')
def download_file(job_id):
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
    app.run(host='0.0.0.0', port=5000, debug=True)
