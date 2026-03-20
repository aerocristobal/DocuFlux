"""Conversion route handlers: Web UI and REST API v1."""

import os
import uuid
import time
import io
import json
import zipfile
import shutil
import logging

from flask import Blueprint, render_template, request, jsonify, session, Response
from werkzeug.utils import secure_filename
from datetime import datetime

import web.app as _app_mod
from formats import FORMATS, detect_format_from_extension
from pandoc_options import validate_pandoc_options

conversion_bp = Blueprint('conversion', __name__)


@conversion_bp.route('/api')
@_app_mod.csrf.exempt
def api_agent_docs():
    """Machine-readable API reference for AI coding agents."""
    base_url = request.host_url.rstrip('/')
    formats_in = [f"{f['key']} ({f['extension']})" for f in FORMATS if f['direction'] in ('Both', 'Input Only')]
    formats_out = [f"{f['key']} ({f['extension']})" for f in FORMATS if f['direction'] in ('Both', 'Output Only')]
    return Response(
        render_template('api_agent_docs.md', base_url=base_url, formats_in=formats_in, formats_out=formats_out),
        mimetype='text/plain; charset=utf-8'
    )


@conversion_bp.route('/')
def index():
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        session.permanent = True
    return render_template('index.html', formats=FORMATS)


@conversion_bp.route('/convert', methods=['POST'])
def convert():
    """Handle multi-file document conversion submissions from the Web UI."""
    if not _app_mod.check_disk_space():
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
    if not session_id:
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
        session.permanent = True
    job_ids = []
    for file in files:
        if file.filename == '': continue
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext != from_info['extension']:
            if not (from_info['key'] == 'markdown' and file_ext in ['.md', '.markdown']):
                 return jsonify({'error': f"Extension {file_ext} mismatch."}), 400

        job_id = str(uuid.uuid4())
        job_dir = os.path.join(_app_mod.app.config['UPLOAD_FOLDER'], job_id)
        os.makedirs(job_dir, exist_ok=True)
        input_filename = secure_filename(file.filename) or f"file_{job_id}"
        input_path = os.path.join(job_dir, input_filename)
        file.save(input_path)

        output_job_dir = os.path.join(_app_mod.app.config['OUTPUT_FOLDER'], job_id)
        os.makedirs(output_job_dir, exist_ok=True)
        base_name = os.path.splitext(input_filename)[0]
        output_filename = f"{base_name}.{to_info['extension'].lstrip('.')}"

        _app_mod.update_job_metadata(job_id, {
            'status': 'PENDING', 'created_at': str(time.time()), 'filename': file.filename,
            'from': from_format, 'to': to_format,
            'force_ocr': str(request.form.get('force_ocr') == 'on'),
            'use_llm': str(request.form.get('use_llm') == 'on')
        })

        file_size = os.path.getsize(input_path)
        target_queue = 'high_priority' if file_size < 5 * 1024 * 1024 else 'default'

        if from_format == 'pdf_marker':
            task_name = 'tasks.convert_with_marker'
        elif from_format == 'pdf_hybrid':
            task_name = 'tasks.convert_with_hybrid'
        elif from_format == 'pdf_marker_slm':
            task_name = 'tasks.convert_with_marker_slm'
        else:
            task_name = 'tasks.convert_document'
        task_args = [job_id, input_filename, output_filename, from_format, to_format]

        if from_format in ('pdf_marker', 'pdf_hybrid', 'pdf_marker_slm'):
            options = {
                'force_ocr': request.form.get('force_ocr') == 'on',
                'use_llm': request.form.get('use_llm') == 'on'
            }
            task_args.append(options)

        _app_mod.celery.send_task(task_name, args=task_args, task_id=job_id, queue=target_queue)

        history_key = f"history:{session_id}"
        _app_mod.redis_client.lpush(history_key, job_id)
        _app_mod.redis_client.expire(history_key, 86400)
        job_ids.append(job_id)

    return jsonify({'job_ids': job_ids, 'status': 'queued'})


@conversion_bp.route('/api/jobs')
def list_jobs():
    """Return the current session's job list with status and download URLs."""
    session_id = session.get('session_id')
    if not session_id: return jsonify([])
    history_key = f"history:{session_id}"
    job_ids = _app_mod.redis_client.lrange(history_key, 0, 49)
    if not job_ids: return jsonify([])

    pipe = _app_mod.redis_client.pipeline()
    for jid in job_ids: pipe.hgetall(f"job:{jid}")
    results = pipe.execute()

    stale_ids = [jid for jid, meta in zip(job_ids, results) if not meta]
    if stale_ids:
        prune_pipe = _app_mod.redis_client.pipeline()
        for jid in stale_ids:
            prune_pipe.lrem(history_key, 0, jid)
        prune_pipe.execute()

    jobs_data = []
    for jid, meta in zip(job_ids, results):
        if not meta: continue

        file_count = 0
        if meta.get('status') == 'SUCCESS':
            cached = meta.get('file_count')
            if cached is not None:
                file_count = int(cached)
            else:
                job_dir = os.path.join(_app_mod.OUTPUT_FOLDER, jid)
                if os.path.exists(job_dir):
                    for root, dirs, files in os.walk(job_dir):
                        file_count += len(files)

        is_zip = file_count > 1
        download_url = None
        if meta.get('status') == 'SUCCESS':
            download_url = f"/download_zip/{jid}" if is_zip else f"/download/{jid}"

        slm = None
        if meta.get('slm_status') == 'SUCCESS':
            try:
                tags = json.loads(meta.get('slm_tags', '[]'))
            except Exception:
                tags = []
            slm = {
                'title': meta.get('slm_title', ''),
                'tags': tags,
                'summary': meta.get('slm_summary', ''),
            }

        jobs_data.append({
            'id': jid, 'filename': meta.get('filename'), 'from': meta.get('from'),
            'to': meta.get('to'), 'created_at': float(meta.get('created_at', 0)),
            'status': meta.get('status', 'PENDING'), 'progress': meta.get('progress', '0'),
            'result': meta.get('error') if meta.get('status') == 'FAILURE' else None,
            'download_url': download_url,
            'is_zip': is_zip,
            'file_count': file_count,
            'slm': slm,
            'stage': meta.get('stage', ''),
            'page_count': meta.get('page_count', ''),
            'started_at': meta.get('started_at', ''),
        })
    jobs_data.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(jobs_data)


@conversion_bp.route('/api/captures')
def list_captures():
    """Return recent browser-extension capture jobs with status and download URLs."""
    job_ids = _app_mod.redis_client.lrange('capture:all_jobs', 0, 49)
    if not job_ids:
        return jsonify([])

    pipe = _app_mod.redis_client.pipeline()
    for jid in job_ids:
        pipe.hgetall(f"job:{jid}")
    results = pipe.execute()

    jobs_data = []
    for jid, meta in zip(job_ids, results):
        if not meta:
            continue
        is_zip = meta.get('is_zip') == 'true'
        download_url = None
        if meta.get('status') == 'SUCCESS':
            download_url = f"/download_zip/{jid}" if is_zip else f"/download/{jid}"
        jobs_data.append({
            'id': jid,
            'filename': meta.get('filename'),
            'from': meta.get('from', 'capture'),
            'to': meta.get('to'),
            'created_at': float(meta.get('created_at', 0)),
            'status': meta.get('status', 'PENDING'),
            'progress': meta.get('progress', '0'),
            'result': meta.get('error') if meta.get('status') == 'FAILURE' else None,
            'download_url': download_url,
            'is_zip': is_zip,
        })
    jobs_data.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(jobs_data)


@conversion_bp.route('/api/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    """Revoke a queued or running Celery task and mark the job REVOKED."""
    if not _app_mod.is_valid_uuid(job_id): return jsonify({'error': 'Invalid ID'}), 400
    _app_mod.celery.control.revoke(job_id, terminate=True)
    _app_mod.update_job_metadata(job_id, {'status': 'REVOKED', 'progress': '0'})
    _app_mod.redis_client.expire(f"job:{job_id}", 600)
    return jsonify({'status': 'cancelled'})


@conversion_bp.route('/api/delete/<job_id>', methods=['POST'])
def delete_job(job_id):
    """Delete a job's files from disk and remove its metadata from Redis."""
    if not _app_mod.is_valid_uuid(job_id): return jsonify({'error': 'Invalid ID'}), 400
    safe_job_id = secure_filename(job_id)
    session_id = session.get('session_id')
    if session_id: _app_mod.redis_client.lrem(f"history:{session_id}", 0, job_id)

    for base in [_app_mod.UPLOAD_FOLDER, _app_mod.OUTPUT_FOLDER]:
        p = os.path.join(base, safe_job_id)
        if os.path.exists(p): shutil.rmtree(p)
    _app_mod.redis_client.delete(f"job:{job_id}")
    return jsonify({'status': 'deleted'})


@conversion_bp.route('/api/retry/<job_id>', methods=['POST'])
def retry_job(job_id):
    """Clone a failed/revoked job and re-queue it with the same parameters."""
    if not _app_mod.is_valid_uuid(job_id): return jsonify({'error': 'Invalid ID'}), 400
    safe_job_id = secure_filename(job_id)
    job_data = _app_mod.redis_client.hgetall(f"job:{job_id}")
    if not job_data: return jsonify({'error': 'Not found'}), 404
    input_filename = job_data.get('filename')
    old_input_path = os.path.join(_app_mod.UPLOAD_FOLDER, safe_job_id, input_filename)
    if not os.path.exists(old_input_path): return jsonify({'error': 'Cleaned up'}), 400

    new_job_id = str(uuid.uuid4())
    new_job_dir = os.path.join(_app_mod.UPLOAD_FOLDER, new_job_id)
    os.makedirs(new_job_dir, exist_ok=True)
    os.makedirs(os.path.join(_app_mod.OUTPUT_FOLDER, new_job_id), exist_ok=True)
    shutil.copy2(old_input_path, os.path.join(new_job_dir, input_filename))

    to_info = next((f for f in FORMATS if f['key'] == job_data.get('to')), None)
    output_filename = f"{os.path.splitext(input_filename)[0]}.{to_info['extension'].lstrip('.')}"

    _app_mod.update_job_metadata(new_job_id, {
        'status': 'PENDING', 'created_at': str(time.time()), 'filename': input_filename,
        'from': job_data.get('from'), 'to': job_data.get('to'),
        'force_ocr': job_data.get('force_ocr'),
        'use_llm': job_data.get('use_llm')
    })

    original_from = job_data.get('from')
    if original_from == 'pdf_marker':
        task_name = 'tasks.convert_with_marker'
    elif original_from == 'pdf_hybrid':
        task_name = 'tasks.convert_with_hybrid'
    elif original_from == 'pdf_marker_slm':
        task_name = 'tasks.convert_with_marker_slm'
    else:
        task_name = 'tasks.convert_document'
    task_args = [new_job_id, input_filename, output_filename, original_from, job_data.get('to')]

    if original_from in ('pdf_marker', 'pdf_hybrid', 'pdf_marker_slm'):
        options = {
            'force_ocr': job_data.get('force_ocr') == 'True',
            'use_llm': job_data.get('use_llm') == 'True'
        }
        task_args.append(options)

    _app_mod.celery.send_task(task_name, args=task_args, task_id=new_job_id)

    session_id = session.get('session_id')
    _app_mod.redis_client.lpush(f"history:{session_id}", new_job_id)
    return jsonify({'status': 'retried', 'new_job_id': new_job_id})


@conversion_bp.route('/download/<job_id>')
def download_file(job_id):
    """Serve the converted output file for download, decrypting if necessary."""
    if not _app_mod.is_valid_uuid(job_id): return "Invalid", 400
    safe_job_id = secure_filename(job_id)
    job_dir = os.path.join(_app_mod.OUTPUT_FOLDER, safe_job_id)
    if not os.path.exists(job_dir): return "Not found", 404
    entries = [e for e in os.listdir(job_dir) if not e.startswith('.')]
    if any(os.path.isdir(os.path.join(job_dir, e)) for e in entries):
        return download_zip(job_id)
    files = [e for e in entries if os.path.isfile(os.path.join(job_dir, e))]
    if not files: return "Not found", 404
    target_file = files[0]
    if len(files) > 1:
        others = [f for f in files if f != 'metadata.json']
        if others: target_file = others[0]

    encrypted_path = os.path.join(job_dir, target_file)
    job_meta = _app_mod.redis_client.hgetall(f"job:{job_id}")
    is_encrypted = job_meta.get('encrypted') == 'true'

    if is_encrypted:
        decrypted_path = _app_mod.decrypt_file_to_temp(encrypted_path, job_id)
        if decrypted_path is None:
            return "Decryption failed", 500

        current_time = str(time.time())
        _app_mod.update_job_metadata(job_id, {
            'downloaded_at': str(time.time()),
            'last_viewed': current_time
        })

        try:
            return _app_mod.send_file(
                decrypted_path,
                as_attachment=True,
                download_name=target_file
            )
        finally:
            if os.path.exists(decrypted_path):
                os.remove(decrypted_path)
    else:
        current_time = str(time.time())
        _app_mod.update_job_metadata(job_id, {
            'downloaded_at': str(time.time()),
            'last_viewed': current_time
        })
        return _app_mod.send_from_directory(job_dir, target_file, as_attachment=True)


@conversion_bp.route('/download_zip/<job_id>')
def download_zip(job_id):
    """Bundle all output files into an in-memory ZIP and serve for download."""
    if not _app_mod.is_valid_uuid(job_id): return "Invalid", 400
    safe_job_id = secure_filename(job_id)
    job_dir = os.path.join(_app_mod.OUTPUT_FOLDER, safe_job_id)
    if not os.path.exists(job_dir): return "Not found", 404

    job_meta = _app_mod.redis_client.hgetall(f"job:{job_id}")
    is_encrypted = job_meta.get('encrypted') == 'true'

    def _generate_zip():
        buf = io.BytesIO()
        temp_files = []
        try:
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(job_dir):
                    for file in files:
                        abs_path = os.path.join(root, file)
                        rel_path = os.path.relpath(abs_path, job_dir)
                        if is_encrypted:
                            decrypted_path = _app_mod.decrypt_file_to_temp(abs_path, job_id)
                            if decrypted_path is None:
                                logging.error(f"Failed to decrypt {abs_path} for zip download")
                                continue
                            zf.write(decrypted_path, rel_path)
                            temp_files.append(decrypted_path)
                        else:
                            zf.write(abs_path, rel_path)
            buf.seek(0)
            yield buf.read()
        finally:
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

    current_time = str(time.time())
    _app_mod.update_job_metadata(job_id, {
        'downloaded_at': str(time.time()),
        'last_viewed': current_time
    })

    orig = job_meta.get('filename', '')
    if orig:
        stem = os.path.splitext(orig)[0]
        zip_name = secure_filename(stem)[:200] + '.zip'
    else:
        zip_name = f'conversion_{job_id}.zip'

    return Response(
        _generate_zip(),
        mimetype='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{zip_name}"',
        }
    )


# ── REST API v1 ─────────────────────────────────────────────────────────────

@conversion_bp.route('/api/v1/convert', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.require_api_key
@_app_mod.limiter.limit("200 per hour")
def api_v1_convert():
    """REST API endpoint for document conversion submission."""
    if not _app_mod.check_disk_space():
        return jsonify({'error': 'Server storage full'}), 507

    if 'file' not in request.files:
        return jsonify({'error': 'Missing required field: file'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    to_format = request.form.get('to_format')
    if not to_format:
        return jsonify({'error': 'Missing required field: to_format'}), 400

    if to_format not in [f['key'] for f in FORMATS if f['direction'] in ['Both', 'Output Only']]:
        return jsonify({'error': f'Unsupported output format: {to_format}'}), 422

    from_format = request.form.get('from_format')
    engine = request.form.get('engine', 'pandoc')
    force_ocr = request.form.get('force_ocr', 'false').lower() == 'true'
    use_llm = request.form.get('use_llm', 'false').lower() == 'true'

    if engine not in ['pandoc', 'marker']:
        return jsonify({'error': f'Invalid engine: {engine}. Must be "pandoc" or "marker"'}), 422

    pandoc_options = None
    raw_pandoc = request.form.get('pandoc_options')
    if raw_pandoc:
        try:
            pandoc_options = json.loads(raw_pandoc)
        except (json.JSONDecodeError, ValueError):
            return jsonify({'error': 'pandoc_options must be valid JSON'}), 400
        if not isinstance(pandoc_options, dict):
            return jsonify({'error': 'pandoc_options must be a JSON object'}), 400
        if engine != 'pandoc':
            return jsonify({'error': 'pandoc_options only valid with engine=pandoc'}), 422
        cleaned, errors = validate_pandoc_options(pandoc_options)
        if errors:
            return jsonify({'error': 'Invalid pandoc_options', 'details': errors}), 422
        pandoc_options = cleaned if cleaned else None

    if not from_format:
        ext = os.path.splitext(file.filename)[1].lower()
        from_format = detect_format_from_extension(ext)
        if not from_format:
            return jsonify({'error': f'Cannot auto-detect format from extension: {ext}'}), 422

    if engine == 'marker' and from_format == 'pdf':
        internal_from_format = 'pdf_marker'
    elif engine == 'hybrid' and from_format == 'pdf':
        internal_from_format = 'pdf_hybrid'
    elif engine == 'marker_slm' and from_format == 'pdf':
        internal_from_format = 'pdf_marker_slm'
    else:
        internal_from_format = from_format

    job_id = str(uuid.uuid4())
    timestamp = str(time.time())

    upload_dir = os.path.join(_app_mod.app.config['UPLOAD_FOLDER'], job_id)
    output_dir = os.path.join(_app_mod.app.config['OUTPUT_FOLDER'], job_id)
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    safe_filename = secure_filename(file.filename)
    input_path = os.path.join(upload_dir, safe_filename)
    file.save(input_path)

    output_filename = os.path.splitext(safe_filename)[0]
    format_info = next((f for f in FORMATS if f['key'] == to_format), None)
    if format_info:
        output_filename += format_info['extension']

    metadata = {
        'status': 'PENDING',
        'filename': safe_filename,
        'from': internal_from_format,
        'to': to_format,
        'engine': engine,
        'created_at': timestamp,
        'progress': '0'
    }

    if engine == 'marker':
        metadata['force_ocr'] = str(force_ocr)
        metadata['use_llm'] = str(use_llm)

    _app_mod.update_job_metadata(job_id, metadata)

    file_size = os.path.getsize(input_path)
    queue_name = 'high_priority' if file_size < 5 * 1024 * 1024 else 'default'

    if internal_from_format == 'pdf_marker':
        options = {'force_ocr': force_ocr, 'use_llm': use_llm}
        _app_mod.celery.send_task(
            'tasks.convert_with_marker',
            args=[job_id, safe_filename, output_filename, internal_from_format, to_format, options],
            queue=queue_name
        )
    elif internal_from_format == 'pdf_hybrid':
        options = {'force_ocr': force_ocr, 'use_llm': use_llm}
        _app_mod.celery.send_task(
            'tasks.convert_with_hybrid',
            args=[job_id, safe_filename, output_filename, internal_from_format, to_format, options],
            queue=queue_name
        )
    elif internal_from_format == 'pdf_marker_slm':
        options = {'force_ocr': force_ocr, 'use_llm': use_llm}
        _app_mod.celery.send_task(
            'tasks.convert_with_marker_slm',
            args=[job_id, safe_filename, output_filename, internal_from_format, to_format, options],
            queue=queue_name
        )
    else:
        task_kwargs = {}
        if pandoc_options:
            task_kwargs['pandoc_options'] = pandoc_options
        _app_mod.celery.send_task(
            'tasks.convert_document',
            args=[job_id, safe_filename, output_filename, internal_from_format, to_format],
            kwargs=task_kwargs,
            queue=queue_name
        )

    return jsonify({
        'job_id': job_id,
        'status': 'queued',
        'status_url': f'/api/v1/status/{job_id}',
        'created_at': datetime.fromtimestamp(float(timestamp)).isoformat() + 'Z'
    }), 202


@conversion_bp.route('/api/v1/status/<job_id>', methods=['GET'])
@_app_mod.csrf.exempt
def api_v1_status(job_id):
    """REST API endpoint for job status retrieval."""
    if not _app_mod.is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job ID format'}), 400

    metadata = _app_mod.get_job_metadata(job_id)
    if not metadata:
        return jsonify({'error': 'Job not found'}), 404

    response = {
        'job_id': job_id,
        'status': metadata.get('status', 'unknown').lower(),
        'progress': int(metadata.get('progress', 0)),
        'filename': metadata.get('filename'),
        'from_format': metadata.get('from'),
        'to_format': metadata.get('to'),
        'engine': metadata.get('engine', 'pandoc'),
        'created_at': datetime.fromtimestamp(float(metadata.get('created_at', 0))).isoformat() + 'Z'
    }

    if 'started_at' in metadata:
        response['started_at'] = datetime.fromtimestamp(float(metadata['started_at'])).isoformat() + 'Z'

    if metadata.get('status') == 'SUCCESS':
        if 'completed_at' in metadata:
            response['completed_at'] = datetime.fromtimestamp(float(metadata['completed_at'])).isoformat() + 'Z'

        _base = os.path.realpath(_app_mod.app.config['OUTPUT_FOLDER'])
        output_dir = os.path.realpath(os.path.join(_app_mod.app.config['OUTPUT_FOLDER'], job_id))
        if not output_dir.startswith(_base + os.sep):
            return jsonify({'error': 'Invalid job path'}), 400
        if os.path.exists(output_dir):
            files = [f for f in os.listdir(output_dir) if f != 'metadata.json']
            is_multifile = len(files) > 1 or os.path.exists(os.path.join(output_dir, 'images'))

            response['download_url'] = f'/api/v1/download/{job_id}'
            response['is_multifile'] = is_multifile
            response['file_count'] = len(files)

            metadata_file = os.path.join(output_dir, 'metadata.json')
            if os.path.exists(metadata_file):
                try:
                    with open(metadata_file, 'r') as f:
                        marker_meta = json.load(f)
                        response['metadata'] = {
                            'pages': marker_meta.get('pages', 0),
                            'images_extracted': len(marker_meta.get('images', [])),
                            'tables_detected': marker_meta.get('table_count', 0)
                        }
                except Exception as e:
                    logging.error(f"Error reading Marker metadata for job {job_id}: {e}")

    if metadata.get('status') == 'FAILURE':
        if 'completed_at' in metadata:
            response['completed_at'] = datetime.fromtimestamp(float(metadata['completed_at'])).isoformat() + 'Z'
        if 'error' in metadata:
            response['error'] = metadata['error']

    slm_status = metadata.get('slm_status')
    if slm_status:
        slm_info = {'status': slm_status}
        if metadata.get('slm_title'):
            slm_info['title'] = metadata['slm_title']
        if metadata.get('slm_tags'):
            try:
                slm_info['tags'] = json.loads(metadata['slm_tags'])
            except Exception:
                slm_info['tags'] = []
        if metadata.get('slm_summary'):
            slm_info['summary'] = metadata['slm_summary']
        response['slm_metadata'] = slm_info

    return jsonify(response), 200


@conversion_bp.route('/api/v1/download/<job_id>', methods=['GET'])
@_app_mod.csrf.exempt
def api_v1_download(job_id):
    """REST API endpoint for downloading converted files."""
    if not _app_mod.is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job ID format'}), 400

    metadata = _app_mod.get_job_metadata(job_id)
    if not metadata:
        return jsonify({'error': 'Job not found'}), 404

    if metadata.get('status') != 'SUCCESS':
        return jsonify({'error': 'Job not completed yet'}), 404

    _base = os.path.realpath(_app_mod.app.config['OUTPUT_FOLDER'])
    output_dir = os.path.realpath(os.path.join(_app_mod.app.config['OUTPUT_FOLDER'], job_id))
    if not output_dir.startswith(_base + os.sep):
        return jsonify({'error': 'Invalid job path'}), 400
    if not os.path.exists(output_dir):
        return jsonify({'error': 'Output files not found or expired'}), 410

    files = [f for f in os.listdir(output_dir) if f != 'metadata.json']
    if not files:
        return jsonify({'error': 'No output files found'}), 404

    _app_mod.update_job_metadata(job_id, {'last_viewed': str(time.time())})

    is_multifile = len(files) > 1 or os.path.exists(os.path.join(output_dir, 'images'))

    if is_multifile:
        return download_zip(job_id)
    else:
        return download_file(job_id)


@conversion_bp.route('/api/v1/formats', methods=['GET'])
@_app_mod.csrf.exempt
def api_v1_formats():
    """REST API endpoint to list supported formats and conversions."""
    input_formats = []
    output_formats = []

    for fmt in FORMATS:
        if fmt['direction'] in ['Both', 'Input Only']:
            input_formats.append({
                'name': fmt['name'],
                'key': fmt['key'],
                'extension': fmt['extension'],
                'mime_types': fmt.get('mime_types', []),
                'supports_marker': fmt['key'] in ['pdf_marker', 'pdf_hybrid'],
                'supports_pandoc': True
            })
        if fmt['direction'] in ['Both', 'Output Only']:
            output_formats.append({
                'name': fmt['name'],
                'key': fmt['key'],
                'extension': fmt['extension']
            })

    conversions = [
        {'from': 'pdf', 'to': 'markdown', 'engines': ['pandoc', 'marker'], 'recommended_engine': 'marker'},
        {'from': 'docx', 'to': 'markdown', 'engines': ['pandoc'], 'recommended_engine': 'pandoc'},
        {'from': 'markdown', 'to': 'pdf', 'engines': ['pandoc'], 'recommended_engine': 'pandoc'},
        {'from': 'markdown', 'to': 'docx', 'engines': ['pandoc'], 'recommended_engine': 'pandoc'},
    ]

    return jsonify({
        'input_formats': input_formats,
        'output_formats': output_formats,
        'conversions': conversions
    }), 200


@conversion_bp.route('/api/v1/jobs/<job_id>/extract-metadata', methods=['POST'])
@_app_mod.csrf.exempt
@_app_mod.require_api_key
def api_v1_extract_metadata(job_id):
    """Manually trigger SLM metadata extraction for a completed job."""
    if not _app_mod.is_valid_uuid(job_id):
        return jsonify({'error': 'Invalid job ID format'}), 400

    metadata = _app_mod.get_job_metadata(job_id)
    if not metadata:
        return jsonify({'error': 'Job not found'}), 404

    if metadata.get('status') != 'SUCCESS':
        return jsonify({'error': 'Job must be in SUCCESS state'}), 409

    output_dir = os.path.realpath(os.path.join(_app_mod.app.config['OUTPUT_FOLDER'], job_id))
    _base = os.path.realpath(_app_mod.app.config['OUTPUT_FOLDER'])
    if not output_dir.startswith(_base + os.sep):
        return jsonify({'error': 'Invalid job path'}), 400

    md_file = None
    if os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            if f.endswith('.md'):
                md_file = os.path.join(output_dir, f)
                break

    if not md_file:
        return jsonify({'error': 'No markdown output found for this job'}), 404

    _app_mod.celery.send_task('tasks.extract_slm_metadata', args=[job_id, md_file], queue='default')
    _app_mod.update_job_metadata(job_id, {'slm_status': 'PENDING'})

    return jsonify({'job_id': job_id, 'status': 'queued', 'message': 'SLM extraction queued'}), 202
