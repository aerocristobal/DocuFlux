"""
Conversion tasks: Pandoc, Marker AI, Marker+SLM, and Hybrid.
"""

import os
import subprocess
import time
import logging
import json

from werkzeug.utils import secure_filename

import tasks as _pkg
from pandoc_options import build_pandoc_cmd


# ── Marker helpers (shared by marker, marker_slm, hybrid) ──────────────────

model_dict = None


def get_model_dict():
    """Lazily load and cache Marker AI model artifacts."""
    if _pkg.model_dict is None:
        os.environ["INFERENCE_RAM"] = "16"
        from marker.models import create_model_dict
        logging.info("Initializing Marker models...")
        _pkg.model_dict = create_model_dict()
        logging.info("Marker models initialized.")
    return _pkg.model_dict


class PageLimitExceeded(Exception):
    """Raised when a PDF exceeds the allowed page limit."""
    pass


def _check_pdf_page_limit(job_id, input_path, max_pages):
    """Check that a PDF does not exceed the page limit for Marker AI."""
    try:
        import pypdfium2 as pdfium
        pdf_doc = pdfium.PdfDocument(input_path)
        page_count = len(pdf_doc)
        pdf_doc.close()
        if page_count > max_pages:
            error_msg = (
                f"PDF has {page_count} pages, which exceeds the {max_pages}-page "
                f"limit for AI conversion. Split the document into smaller parts."
            )
            _pkg.update_job_metadata(job_id, {
                'status': 'FAILURE', 'completed_at': str(time.time()),
                'error': error_msg, 'progress': '0', 'stage': 'Failed'
            })
            _pkg.redis_client.expire(f"job:{job_id}", 600)
            _pkg.fire_webhook(job_id, 'FAILURE', {'error': error_msg})
            raise PageLimitExceeded(error_msg)
        _pkg.update_job_metadata(job_id, {'page_count': str(page_count)})
        logging.info(f"PDF page count: {page_count} (limit: {max_pages})")
    except PageLimitExceeded:
        raise
    except Exception as e:
        logging.warning(f"Could not check PDF page count: {e}")


def _run_marker(input_path, options):
    """Load Marker models and run PDF conversion."""
    from marker.converters.pdf import PdfConverter
    artifacts = _pkg.get_model_dict()
    converter = PdfConverter(artifact_dict=artifacts, config=options)
    logging.info(f"Running Marker conversion on {input_path}")
    rendered = converter(input_path)
    return converter, rendered


def _save_marker_output(rendered, output_path, images_dir):
    """Extract text and images from a Marker result and write to disk."""
    from marker.output import text_from_rendered

    text, _, images = text_from_rendered(rendered)

    saved_images_count = 0
    for filename, image in images.items():
        image.save(os.path.join(images_dir, filename))
        saved_images_count += 1
        text = text.replace(f"({filename})", f"(images/{filename})")
    logging.info(f"Saved {saved_images_count} images to {images_dir}")

    with open(output_path, "w", encoding='utf-8') as f:
        f.write(text)

    metadata_path = os.path.join(os.path.dirname(output_path), "metadata.json")
    with open(metadata_path, "w", encoding='utf-8') as f:
        json.dump(rendered.metadata, f, indent=2, default=str)

    file_count = 2 + saved_images_count
    return text, images, saved_images_count, file_count


def _cleanup_marker_memory(*objects):
    """Free GPU/CPU memory after a Marker task."""
    import gc
    for obj in objects:
        try:
            del obj
        except Exception:
            pass
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            mem_freed = torch.cuda.memory_reserved(0) - torch.cuda.memory_allocated(0)
            logging.info(f"Memory cleanup complete. GPU memory freed: {mem_freed / 1e9:.2f} GB")
        else:
            logging.info("Memory cleanup complete (CPU mode)")
    except Exception as e:
        logging.warning(f"Memory cleanup failed: {e}")


def _slm_refine_markdown(text, job_id):
    """Run the SLM over Marker output in ~600-word chunks to fix OCR artifacts."""
    from warmup import get_slm_model
    slm = get_slm_model()
    if slm is None:
        logging.warning(f"[{job_id}] SLM not loaded; skipping OCR refinement")
        return text

    SYSTEM = (
        "You are a document editor. The text below was extracted from a PDF by OCR. "
        "Fix ONLY: character confusion (l/1/I, O/0), broken hyphen-ation across lines, "
        "and page numbers or running headers that leaked into body text. "
        "Do NOT change meaning, content, or Markdown structure. "
        "Return only the corrected text.\n\nText:\n"
    )
    CHUNK_WORDS = 600

    words = text.split()
    chunks = [words[i:i + CHUNK_WORDS] for i in range(0, len(words), CHUNK_WORDS)]
    refined_chunks = []
    for i, chunk in enumerate(chunks):
        chunk_text = " ".join(chunk)
        prompt = SYSTEM + chunk_text + "\n\nCorrected text:\n"
        try:
            out = slm.create_completion(prompt, max_tokens=800, temperature=0.1,
                                        top_p=0.9, stop=["---"])
            refined_chunks.append(out['choices'][0]['text'].strip())
        except Exception as e:
            logging.warning(f"[{job_id}] SLM chunk {i} failed: {e}; using original")
            refined_chunks.append(chunk_text)
        _pkg.update_job_metadata(job_id, {'progress': str(50 + int(40 * (i + 1) / len(chunks))), 'stage': 'Refining text with language model'})

    return "\n\n".join(refined_chunks)


def _assess_pandoc_quality(output_path, page_count):
    """Return True if Pandoc's PDF->markdown output meets a minimum quality threshold."""
    try:
        with open(output_path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        word_count = len(text.split())
        words_per_page = word_count / max(page_count, 1)
        logging.info(f"Pandoc quality: {word_count} words / {page_count} pages = {words_per_page:.1f} words/page")
        return words_per_page >= 50
    except Exception as e:
        logging.warning(f"Quality assessment failed: {e}")
        return False


# ── Celery tasks ────────────────────────────────────────────────────────────

@_pkg.celery.task(
    name='tasks.convert_document',
    time_limit=600,
    soft_time_limit=540,
    acks_late=True,
    reject_on_worker_lost=True
)
def convert_document(job_id, input_filename, output_filename, from_format, to_format, pandoc_options=None):
    """Convert a document using Pandoc."""
    from metrics import worker_tasks_active, conversion_total, conversion_failures_total, conversion_duration_seconds

    worker_tasks_active.inc()
    start_time = time.time()

    try:
        if not _pkg.is_valid_uuid(job_id):
            logging.error(f"Invalid job_id received: {job_id}")
            return {"status": "error", "message": "Invalid job ID"}

        current_status = _pkg.redis_client.hget(f"job:{job_id}", 'status')
        if current_status in ('FAILURE', 'REVOKED'):
            logging.warning(f"Skipping re-queued task for already-{current_status} job {job_id}")
            worker_tasks_active.dec()
            return {"status": "skipped", "reason": current_status}

        safe_job_id = secure_filename(job_id)
        safe_input_filename = secure_filename(input_filename)
        safe_output_filename = secure_filename(output_filename)

        input_path = os.path.join(_pkg.UPLOAD_FOLDER, safe_job_id, safe_input_filename)
        output_path = os.path.join(_pkg.OUTPUT_FOLDER, safe_job_id, safe_output_filename)

        logging.info(f"Starting conversion for job {job_id}: {from_format} -> {to_format}")
        _pkg.update_job_metadata(job_id, {
            'status': 'PROCESSING',
            'started_at': str(time.time()),
            'progress': '10',
            'stage': 'Preparing conversion'
        })
    except Exception:
        worker_tasks_active.dec()
        raise

    if not os.path.exists(input_path):
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    _pkg.update_job_metadata(job_id, {'progress': '20', 'stage': 'Converting document'})

    cmd = build_pandoc_cmd(from_format, to_format, input_path, output_path, pandoc_options)

    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=500)
        logging.info(f"Conversion successful: {output_path}")

        _pkg.update_job_metadata(job_id, {
            'status': 'SUCCESS',
            'completed_at': str(time.time()),
            'progress': '100',
            'encrypted': 'false',
            'file_count': '1',
            'stage': 'Complete'
        })
        _pkg.redis_client.expire(f"job:{job_id}", 7200)
        _pkg.fire_webhook(job_id, 'SUCCESS', {'download_url': f'/api/v1/download/{job_id}'})

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        import gc
        gc.collect()

        return {"status": "success", "output_file": os.path.basename(output_path)}
    except subprocess.TimeoutExpired:
        error_msg = "Conversion timed out after 500 seconds"
        logging.error(f"Timeout for job {job_id}: {error_msg}")
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': error_msg, 'progress': '0', 'stage': 'Failed'})
        _pkg.redis_client.expire(f"job:{job_id}", 600)
        _pkg.fire_webhook(job_id, 'FAILURE', {'error': error_msg})

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='timeout').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        raise Exception(error_msg)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr or e.stdout or "Unknown error"
        logging.error(f"Pandoc error for job {job_id}: {error_msg}")
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(error_msg)[:500], 'progress': '0', 'stage': 'Failed'})
        _pkg.redis_client.expire(f"job:{job_id}", 600)
        _pkg.fire_webhook(job_id, 'FAILURE', {'error': str(error_msg)[:500]})

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='pandoc_error').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        raise Exception(f"Pandoc failed: {error_msg}")
    except Exception as e:
        logging.error(f"Unexpected error for job {job_id}: {str(e)}")
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0', 'stage': 'Failed'})
        _pkg.redis_client.expire(f"job:{job_id}", 600)
        _pkg.fire_webhook(job_id, 'FAILURE', {'error': str(e)[:500]})

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='unknown').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        raise


@_pkg.celery.task(
    name='tasks.convert_with_marker',
    bind=True,
    time_limit=1200,
    soft_time_limit=1140,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3
)
def convert_with_marker(self, job_id, input_filename, output_filename, from_format, to_format, options=None):
    """Convert a PDF to Markdown using Marker AI (GPU-accelerated deep learning)."""
    from metrics import worker_tasks_active, conversion_total, conversion_failures_total, conversion_duration_seconds

    worker_tasks_active.inc()
    start_time = time.time()

    if not _pkg.is_valid_uuid(job_id):
        logging.error(f"Invalid job_id received: {job_id}")
        worker_tasks_active.dec()
        return {"status": "error", "message": "Invalid job ID"}

    current_status = _pkg.redis_client.hget(f"job:{job_id}", 'status')
    if current_status in ('FAILURE', 'REVOKED'):
        logging.warning(f"Skipping re-queued task for already-{current_status} job {job_id}")
        worker_tasks_active.dec()
        return {"status": "skipped", "reason": current_status}

    safe_job_id = secure_filename(job_id)
    input_path = os.path.join(_pkg.UPLOAD_FOLDER, safe_job_id, secure_filename(input_filename))
    output_dir = os.path.join(_pkg.OUTPUT_FOLDER, safe_job_id)
    output_path = os.path.join(output_dir, secure_filename(output_filename))

    logging.info(f"Starting Marker conversion for job {job_id} (Attempt {self.request.retries + 1}) with options: {options}")
    _pkg.update_job_metadata(job_id, {'status': 'PROCESSING', 'started_at': str(time.time()), 'progress': '5', 'stage': 'Preparing conversion'})

    if not os.path.exists(input_path):
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

    try:
        _pkg._check_pdf_page_limit(job_id, input_path, _pkg.app_settings.max_marker_pages)
    except PageLimitExceeded:
        worker_tasks_active.dec()
        raise

    os.makedirs(output_dir, exist_ok=True)
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    converter = rendered = text = images = None
    try:
        _pkg.update_job_metadata(job_id, {'progress': '15', 'stage': 'Converting PDF with AI'})
        converter, rendered = _pkg._run_marker(input_path, options or {})

        _pkg.update_job_metadata(job_id, {'progress': '80', 'stage': 'Saving extracted content'})
        text, images, _, file_count = _pkg._save_marker_output(rendered, output_path, images_dir)

        _pkg.update_job_metadata(job_id, {'progress': '90', 'file_count': str(file_count), 'stage': 'Finalizing output'})
        logging.info(f"Marker conversion successful: {output_path}")

        _pkg.update_job_metadata(job_id, {
            'status': 'SUCCESS', 'completed_at': str(time.time()),
            'progress': '100', 'encrypted': 'false', 'stage': 'Complete'
        })
        _pkg.redis_client.expire(f"job:{job_id}", 7200)
        _pkg.fire_webhook(job_id, 'SUCCESS', {'download_url': f'/api/v1/download/{job_id}'})
        _pkg.extract_slm_metadata.delay(job_id, output_path)

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        _pkg._cleanup_marker_memory(converter, rendered, text, images)
        return {"status": "success", "output_file": os.path.basename(output_path)}

    except Exception as e:
        logging.error(f"Marker error for job {job_id}: {e}")
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0', 'stage': 'Failed'})
        _pkg.redis_client.expire(f"job:{job_id}", 600)
        _pkg.fire_webhook(job_id, 'FAILURE', {'error': str(e)[:500]})

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='marker_error').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        _pkg._cleanup_marker_memory(converter, rendered, text, images)
        raise


@_pkg.celery.task(
    name='tasks.convert_with_marker_slm',
    bind=True,
    time_limit=1500,
    soft_time_limit=1380,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=1,
)
def convert_with_marker_slm(self, job_id, input_filename, output_filename,
                             from_format, to_format, options=None):
    """Convert a PDF via Marker AI then run an SLM pass to fix OCR artifacts."""
    from metrics import worker_tasks_active, conversion_total, conversion_failures_total, conversion_duration_seconds

    worker_tasks_active.inc()
    start_time = time.time()

    if not _pkg.is_valid_uuid(job_id):
        logging.error(f"Invalid job_id received: {job_id}")
        worker_tasks_active.dec()
        return {"status": "error", "message": "Invalid job ID"}

    current_status = _pkg.redis_client.hget(f"job:{job_id}", 'status')
    if current_status in ('FAILURE', 'REVOKED'):
        logging.warning(f"Skipping re-queued task for already-{current_status} job {job_id}")
        worker_tasks_active.dec()
        return {"status": "skipped", "reason": current_status}

    safe_job_id = secure_filename(job_id)
    input_path = os.path.join(_pkg.UPLOAD_FOLDER, safe_job_id, secure_filename(input_filename))
    output_dir = os.path.join(_pkg.OUTPUT_FOLDER, safe_job_id)
    output_path = os.path.join(output_dir, secure_filename(output_filename))

    logging.info(f"Starting Marker+SLM conversion for job {job_id} with options: {options}")
    _pkg.update_job_metadata(job_id, {'status': 'PROCESSING', 'started_at': str(time.time()), 'progress': '5', 'stage': 'Preparing conversion'})

    if not os.path.exists(input_path):
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        raise FileNotFoundError(f"Input file not found: {input_path}")

    try:
        _pkg._check_pdf_page_limit(job_id, input_path, _pkg.app_settings.max_marker_pages)
    except PageLimitExceeded:
        worker_tasks_active.dec()
        raise

    os.makedirs(output_dir, exist_ok=True)
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    converter = rendered = text = images = None
    try:
        _pkg.update_job_metadata(job_id, {'progress': '15', 'stage': 'Converting PDF with AI'})
        converter, rendered = _pkg._run_marker(input_path, options or {})

        _pkg.update_job_metadata(job_id, {'progress': '50', 'stage': 'Saving AI conversion output'})
        text, images, _, file_count = _pkg._save_marker_output(rendered, output_path, images_dir)

        _pkg._cleanup_marker_memory(converter, rendered)
        converter = rendered = None

        logging.info(f"[{job_id}] Starting SLM refinement pass")
        refined_text = _pkg._slm_refine_markdown(text, job_id)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(refined_text)

        _pkg.update_job_metadata(job_id, {'progress': '95', 'file_count': str(file_count), 'stage': 'Finalizing output'})
        logging.info(f"Marker+SLM conversion successful: {output_path}")

        _pkg.update_job_metadata(job_id, {
            'status': 'SUCCESS', 'completed_at': str(time.time()),
            'progress': '100', 'encrypted': 'false', 'stage': 'Complete'
        })
        _pkg.redis_client.expire(f"job:{job_id}", 7200)
        _pkg.fire_webhook(job_id, 'SUCCESS', {'download_url': f'/api/v1/download/{job_id}'})
        _pkg.extract_slm_metadata.delay(job_id, output_path)

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        _pkg._cleanup_marker_memory(text, images)
        return {"status": "success", "output_file": os.path.basename(output_path)}

    except Exception as e:
        logging.error(f"Marker+SLM error for job {job_id}: {e}")
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0', 'stage': 'Failed'})
        _pkg.redis_client.expire(f"job:{job_id}", 600)
        _pkg.fire_webhook(job_id, 'FAILURE', {'error': str(e)[:500]})

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='marker_slm_error').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()

        _pkg._cleanup_marker_memory(converter, rendered, text, images)
        raise


@_pkg.celery.task(
    name='tasks.convert_with_hybrid',
    bind=True,
    time_limit=1200,
    soft_time_limit=1140,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3
)
def convert_with_hybrid(self, job_id, input_filename, output_filename, from_format, to_format, options=None):
    """Convert a PDF using Pandoc first, falling back to Marker AI if quality is poor."""
    from metrics import worker_tasks_active, conversion_total, conversion_failures_total, conversion_duration_seconds

    worker_tasks_active.inc()
    start_time = time.time()

    if not _pkg.is_valid_uuid(job_id):
        logging.error(f"Invalid job_id received: {job_id}")
        worker_tasks_active.dec()
        return {"status": "error", "message": "Invalid job ID"}

    current_status = _pkg.redis_client.hget(f"job:{job_id}", 'status')
    if current_status in ('FAILURE', 'REVOKED'):
        logging.warning(f"Skipping re-queued task for already-{current_status} job {job_id}")
        worker_tasks_active.dec()
        return {"status": "skipped", "reason": current_status}

    safe_job_id = secure_filename(job_id)
    input_path = os.path.join(_pkg.UPLOAD_FOLDER, safe_job_id, secure_filename(input_filename))
    output_dir = os.path.join(_pkg.OUTPUT_FOLDER, safe_job_id)
    output_path = os.path.join(output_dir, secure_filename(output_filename))

    logging.info(f"Starting hybrid conversion for job {job_id}")
    _pkg.update_job_metadata(job_id, {'status': 'PROCESSING', 'started_at': str(time.time()), 'progress': '5', 'stage': 'Preparing conversion'})

    if not os.path.exists(input_path):
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': 'Input file missing'})
        worker_tasks_active.dec()
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(output_dir, exist_ok=True)

    # Get PDF page count
    page_count = 1
    try:
        import pypdfium2 as pdfium
        pdf_doc = pdfium.PdfDocument(input_path)
        page_count = len(pdf_doc)
        pdf_doc.close()
        _pkg.update_job_metadata(job_id, {'page_count': str(page_count)})
    except Exception as e:
        logging.warning(f"Could not get page count: {e}")

    # Try Pandoc fast path
    _pkg.update_job_metadata(job_id, {'progress': '15', 'hybrid_engine_used': 'pandoc', 'stage': 'Trying fast conversion'})
    pandoc_ok = False
    try:
        cmd = ['pandoc', '-f', 'pdf', '-t', 'markdown', input_path, '-o', output_path]
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        pandoc_ok = _pkg._assess_pandoc_quality(output_path, page_count)
        if pandoc_ok:
            logging.info(f"Hybrid job {job_id}: Pandoc output is high quality, done.")
    except Exception as e:
        logging.info(f"Hybrid job {job_id}: Pandoc attempt failed ({e}), trying Marker.")

    _pkg.update_job_metadata(job_id, {'progress': '40', 'stage': 'Checking conversion quality'})

    if pandoc_ok:
        _pkg.update_job_metadata(job_id, {
            'status': 'SUCCESS', 'completed_at': str(time.time()),
            'progress': '100', 'encrypted': 'false', 'file_count': '1',
            'hybrid_engine_used': 'pandoc', 'stage': 'Complete'
        })
        _pkg.redis_client.expire(f"job:{job_id}", 7200)
        _pkg.fire_webhook(job_id, 'SUCCESS', {'download_url': f'/api/v1/download/{job_id}'})
        _pkg.extract_slm_metadata.delay(job_id, output_path)

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()
        import gc; gc.collect()
        return {"status": "success", "output_file": os.path.basename(output_path), "engine": "pandoc"}

    # Fall back to Marker AI
    try:
        _pkg._check_pdf_page_limit(job_id, input_path, _pkg.app_settings.max_marker_pages)
    except PageLimitExceeded:
        worker_tasks_active.dec()
        raise

    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    _pkg.update_job_metadata(job_id, {'progress': '50', 'hybrid_engine_used': 'marker', 'stage': 'Converting PDF with AI'})
    converter = rendered = text = images = None
    try:
        converter, rendered = _pkg._run_marker(input_path, options or {})
        _pkg.update_job_metadata(job_id, {'progress': '85', 'stage': 'Saving extracted content'})
        text, images, _, file_count = _pkg._save_marker_output(rendered, output_path, images_dir)

        _pkg.update_job_metadata(job_id, {
            'status': 'SUCCESS', 'completed_at': str(time.time()),
            'progress': '100', 'encrypted': 'false', 'file_count': str(file_count),
            'hybrid_engine_used': 'marker', 'stage': 'Complete'
        })
        _pkg.redis_client.expire(f"job:{job_id}", 7200)
        _pkg.fire_webhook(job_id, 'SUCCESS', {'download_url': f'/api/v1/download/{job_id}'})
        _pkg.extract_slm_metadata.delay(job_id, output_path)

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='success').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()
        _pkg._cleanup_marker_memory(converter, rendered, text, images)
        return {"status": "success", "output_file": os.path.basename(output_path), "engine": "marker"}

    except Exception as e:
        logging.error(f"Hybrid Marker fallback error for job {job_id}: {e}")
        _pkg.update_job_metadata(job_id, {'status': 'FAILURE', 'completed_at': str(time.time()), 'error': str(e)[:500], 'progress': '0', 'stage': 'Failed'})
        _pkg.redis_client.expire(f"job:{job_id}", 600)
        _pkg.fire_webhook(job_id, 'FAILURE', {'error': str(e)[:500]})

        duration = time.time() - start_time
        conversion_total.labels(format_from=from_format, format_to=to_format, status='failure').inc()
        conversion_failures_total.labels(format_from=from_format, format_to=to_format, error_type='hybrid_error').inc()
        conversion_duration_seconds.labels(format_from=from_format, format_to=to_format).observe(duration)
        worker_tasks_active.dec()
        _pkg._cleanup_marker_memory(converter, rendered, text, images)
        raise
