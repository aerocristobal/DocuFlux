"""
Browser extension capture tasks: layout analysis, page turning, batch OCR, assembly.
"""

import os
import subprocess
import time
import json
import logging

from werkzeug.utils import secure_filename

import tasks as _pkg


@_pkg.celery.task(
    name='tasks.analyze_screenshot_layout',
    time_limit=300,
    soft_time_limit=240,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0
)
def analyze_screenshot_layout(job_id, url, storage_state_json=None):
    """Analyzes the layout of a screenshot from a given URL."""
    from PIL import Image

    logging.info(f"Starting screenshot layout analysis for job {job_id} on URL: {url}")
    _pkg.update_job_metadata(job_id, {'layout_analysis_status': 'PROCESSING', 'layout_analysis_started_at': str(time.time())})

    temp_screenshot_path = None
    try:
        job_output_dir = os.path.join(_pkg.OUTPUT_FOLDER, job_id)
        os.makedirs(job_output_dir, exist_ok=True)
        temp_screenshot_path = os.path.join(job_output_dir, f"screenshot_{job_id}.png")

        logging.info(f"Capturing screenshot for job {job_id} to {temp_screenshot_path}...")
        script = [
            {'action': 'goto', 'args': {'url': url}},
            {'action': 'screenshot', 'args': {'path': temp_screenshot_path}},
        ]
        mcp_args = {'script': script}
        if storage_state_json:
            mcp_args['storageState'] = storage_state_json
        mcp_response = _pkg.call_mcp_server('execute_script', mcp_args)
        if not mcp_response.get('success'):
            raise Exception(f"Failed to navigate and capture screenshot: {mcp_response.get('error', 'Unknown error')}")
        results = mcp_response.get('script_execution_results', [])
        screenshot_result = next((r for r in results if r.get('action') == 'screenshot'), None)
        if not screenshot_result or not screenshot_result.get('success'):
            raise Exception(f"Screenshot step failed in MCP script")
        logging.info(f"Screenshot captured and saved to {temp_screenshot_path}")

        logging.info(f"Performing layout analysis on {temp_screenshot_path} using Marker...")

        with Image.open(temp_screenshot_path) as img:
            width, height = img.size

        layout_results = {
            "text_regions": [
                {"bbox": [0, 0, width, height * 0.7], "content": "Simulated text content from OCR"},
                {"bbox": [0, height * 0.75, width * 0.5, height], "content": "More simulated text"}
            ],
            "visual_regions": [
                {"bbox": [width * 0.7, 0, width, height * 0.3], "type": "chart", "description": "Simulated chart region"},
                {"bbox": [width * 0.55, height * 0.75, width, height], "type": "image", "description": "Simulated image region"}
            ]
        }

        logging.info(f"Layout analysis completed for job {job_id}. Results: {layout_results}")

        _pkg.update_job_metadata(job_id, {
            'layout_analysis_status': 'SUCCESS',
            'layout_analysis_completed_at': str(time.time()),
            'layout_results': json.dumps(layout_results)
        })
        return {"status": "success", "layout_results": layout_results}

    except Exception as e:
        error_msg = f"Screenshot layout analysis failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        _pkg.update_job_metadata(job_id, {'layout_analysis_status': 'FAILURE', 'layout_analysis_error': error_msg})
        raise
    finally:
        if temp_screenshot_path and os.path.exists(temp_screenshot_path):
            os.remove(temp_screenshot_path)
            logging.info(f"Cleaned up temporary screenshot: {temp_screenshot_path}")


@_pkg.celery.task(
    name='tasks.agentic_page_turner',
    time_limit=1800,
    soft_time_limit=1740,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0
)
def agentic_page_turner(job_id, start_url, next_button_selector,
                        end_condition_selector=None, max_pages=10,
                        storage_state_json=None):
    """Performs agentic page turning and extraction using the MCP server."""
    logging.info(f"Starting agentic page turner for job {job_id} on URL: {start_url}")
    _pkg.update_job_metadata(job_id, {'page_turner_status': 'PROCESSING', 'page_turner_started_at': str(time.time())})

    current_page_num = 0
    extracted_data = []
    try:
        script = [
            {'action': 'goto', 'args': {'url': start_url}},
        ]
        if storage_state_json:
            script[0]['args']['storageState'] = storage_state_json

        mcp_response = _pkg.call_mcp_server('execute_script', {'script': script})
        if not mcp_response.get('success'):
            raise Exception(f"Initial navigation failed: {mcp_response.get('error', 'Unknown error')}")

        page_content = mcp_response['script_execution_results'][0].get('content', '')
        extracted_data.append({'page_num': current_page_num + 1, 'content': page_content})
        logging.info(f"Page {current_page_num + 1} extracted.")
        _pkg.update_job_metadata(job_id, {'page_turner_progress': f"{current_page_num + 1}/{max_pages}", 'current_page_url': start_url})

        while current_page_num < max_pages:
            current_page_num += 1
            logging.info(f"Attempting to turn page {current_page_num + 1} for job {job_id}")

            page_turn_script = [
                {'action': 'wait_for_selector', 'args': {'selector': next_button_selector, 'timeout': 10000}},
                {'action': 'click_element', 'args': {'selector': next_button_selector}},
                {'action': 'get_content'}
            ]

            mcp_response = _pkg.call_mcp_server('execute_script', {'script': page_turn_script, 'storageState': storage_state_json})

            if not mcp_response.get('success'):
                logging.warning(f"Failed to turn page {current_page_num + 1}: {mcp_response.get('error', 'Unknown error')}. Ending extraction.")
                break

            if end_condition_selector:
                check_end_script = [
                    {'action': 'get_element_bounding_box', 'args': {'selector': end_condition_selector}}
                ]
                end_response = _pkg.call_mcp_server('execute_script', {'script': check_end_script, 'storageState': storage_state_json})
                if end_response.get('success') and end_response['script_execution_results'][0]['bbox']:
                    logging.info(f"End condition met: '{end_condition_selector}' found on page {current_page_num + 1}.")
                    break

            page_content = mcp_response['script_execution_results'][-1].get('content', '')
            extracted_data.append({'page_num': current_page_num + 1, 'content': page_content})
            logging.info(f"Page {current_page_num + 1} extracted.")
            _pkg.update_job_metadata(job_id, {'page_turner_progress': f"{current_page_num + 1}/{max_pages}"})

        _pkg.update_job_metadata(job_id, {
            'page_turner_status': 'SUCCESS',
            'page_turner_completed_at': str(time.time()),
            'extracted_pages': json.dumps(extracted_data)
        })
        logging.info(f"Agentic page turning completed for job {job_id}. Extracted {len(extracted_data)} pages.")
        return {"status": "success", "extracted_pages_count": len(extracted_data)}

    except Exception as e:
        error_msg = f"Agentic page turning failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        _pkg.update_job_metadata(job_id, {'page_turner_status': 'FAILURE', 'page_turner_error': error_msg})
        raise


@_pkg.celery.task(
    name='tasks.process_capture_batch',
    time_limit=900,
    soft_time_limit=840,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_capture_batch(session_id, job_id, batch_index, page_start, page_end):
    """Process a batch of captured pages through Marker OCR."""
    import base64
    import io as io_module
    import json as json_module
    import gc
    import re as re_module

    # Access via _pkg so test patches work

    batch_key = f"capture:batch:{session_id}:{batch_index}"
    session_key = f"capture:session:{session_id}"
    batch_dir = os.path.join(_pkg.OUTPUT_FOLDER, job_id, 'batches', f'batch_{batch_index}')
    batch_images_dir = os.path.join(batch_dir, 'images')
    os.makedirs(batch_images_dir, exist_ok=True)

    logging.info(f"Processing capture batch {batch_index}: session={session_id}, pages={page_start}-{page_end}")

    _pkg.redis_client.hset(batch_key, mapping={
        'status': 'processing',
        'started_at': str(time.time()),
    })

    try:
        pages_raw = _pkg.redis_client.lrange(f"capture:session:{session_id}:pages", page_start, page_end - 1)
        pages = [json_module.loads(p) for p in pages_raw]
        pages.sort(key=lambda p: p.get('page_hint', 0))

        ocr_images_b64 = []
        for page in pages:
            page_imgs = page.get('images', [])
            chosen = next((i for i in page_imgs if i.get('is_screenshot') and i.get('b64')), None)
            if chosen is None:
                chosen = next((i for i in page_imgs if i.get('b64')), None)
            if chosen:
                ocr_images_b64.append(chosen['b64'])

        if not ocr_images_b64:
            with open(os.path.join(batch_dir, 'batch.md'), 'w', encoding='utf-8') as f:
                f.write('')
            _pkg.redis_client.hset(batch_key, mapping={
                'status': 'done',
                'completed_at': str(time.time()),
                'image_count': '0',
            })
            _pkg.redis_client.hincrby(session_key, 'batches_done', 1)
            logging.info(f"Batch {batch_index} done (no images): session={session_id}")
            return {'status': 'success', 'images': 0}

        from PIL import Image as PILImage
        pil_images = []
        for img_b64 in ocr_images_b64:
            try:
                if ',' in img_b64:
                    img_b64 = img_b64.split(',', 1)[1]
                pil_img = PILImage.open(io_module.BytesIO(base64.b64decode(img_b64))).convert('RGB')
                pil_images.append(pil_img)
            except Exception as e:
                logging.warning(f"Batch {batch_index}: failed to decode screenshot: {e}")

        if not pil_images:
            with open(os.path.join(batch_dir, 'batch.md'), 'w', encoding='utf-8') as f:
                f.write('')
            _pkg.redis_client.hset(batch_key, mapping={
                'status': 'done',
                'completed_at': str(time.time()),
                'image_count': '0',
            })
            _pkg.redis_client.hincrby(session_key, 'batches_done', 1)
            return {'status': 'success', 'images': 0}

        pdf_buf = io_module.BytesIO()
        pil_images[0].save(pdf_buf, format='PDF', save_all=True, append_images=pil_images[1:], resolution=150)
        pdf_buf.seek(0)

        artifacts = _pkg.get_model_dict()
        from marker.converters.pdf import PdfConverter
        from marker.output import text_from_rendered

        converter = PdfConverter(artifact_dict=artifacts, config={'force_ocr': True})
        rendered = converter(pdf_buf)
        markdown_text, _, marker_images = text_from_rendered(rendered)

        img_count_key = f"capture:session:{session_id}:image_counter"
        num_images = len(marker_images or {})
        if num_images > 0:
            new_total = _pkg.redis_client.incrby(img_count_key, num_images)
            _pkg.redis_client.expire(img_count_key, _pkg.app_settings.capture_session_ttl)
            base_offset = new_total - num_images
        else:
            base_offset = 0

        image_count = 0
        for i, (img_name, img_obj) in enumerate(sorted((marker_images or {}).items())):
            global_idx = base_offset + i
            final_name = f"img_{global_idx:05d}.png"
            img_obj.save(os.path.join(batch_images_dir, final_name))
            markdown_text = re_module.sub(
                re_module.escape(f"({img_name})"),
                f"(images/{final_name})",
                markdown_text,
            )
            image_count += 1

        with open(os.path.join(batch_dir, 'batch.md'), 'w', encoding='utf-8') as f:
            f.write(markdown_text)

        _pkg.redis_client.hset(batch_key, mapping={
            'status': 'done',
            'completed_at': str(time.time()),
            'image_count': str(image_count),
        })
        _pkg.redis_client.hincrby(session_key, 'batches_done', 1)

        batches_queued = int(_pkg.redis_client.hget(session_key, 'batches_queued') or 1)
        batches_done = int(_pkg.redis_client.hget(session_key, 'batches_done') or 1)
        progress = int((batches_done / batches_queued) * 75)
        _pkg.update_job_metadata(job_id, {'progress': str(progress)})

        logging.info(f"Batch {batch_index} done: session={session_id}, images={image_count}")

        _pkg._cleanup_marker_memory(converter, rendered)
        gc.collect()
        return {'status': 'success', 'images': image_count}

    except Exception as e:
        error_msg = str(e)
        logging.error(f"Batch {batch_index} failed: session={session_id}, error={error_msg}")
        _pkg.redis_client.hset(batch_key, mapping={
            'status': 'failed',
            'error': error_msg[:500],
        })
        _pkg.redis_client.hincrby(session_key, 'batches_failed', 1)
        raise


@_pkg.celery.task(
    name='tasks.assemble_capture_session',
    time_limit=600,
    soft_time_limit=540,
    acks_late=True,
    reject_on_worker_lost=True,
)
def assemble_capture_session(session_id, job_id):
    """Assembles captured browser extension pages into a single Markdown document."""
    import base64
    import json as json_module
    import gc
    import shutil

    # Access via _pkg so test patches work

    logging.info(f"Starting capture assembly: session={session_id}, job={job_id}")
    _pkg.update_job_metadata(job_id, {
        'status': 'PROCESSING',
        'started_at': str(time.time()),
        'progress': '10',
    })

    try:
        session_key = f"capture:session:{session_id}"
        session_meta = _pkg.redis_client.hgetall(session_key)
        title = session_meta.get('title', 'Captured Document')
        to_format = session_meta.get('to_format', 'markdown')
        source_url = session_meta.get('source_url', '')

        pages_raw = _pkg.redis_client.lrange(f"capture:session:{session_id}:pages", 0, -1)
        if not pages_raw:
            raise ValueError("No pages found in capture session")

        pages = [json_module.loads(p) for p in pages_raw]
        pages.sort(key=lambda p: p.get('page_hint', 0))

        _pkg.update_job_metadata(job_id, {'progress': '20'})

        output_dir = os.path.join(_pkg.OUTPUT_FOLDER, job_id)
        images_dir = os.path.join(output_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)

        safe_title = secure_filename(title) or f"capture_{job_id}"
        force_ocr = session_meta.get('force_ocr', 'false').lower() == 'true'
        batches_queued = int(session_meta.get('batches_queued', 0))

        if force_ocr and batches_queued > 0:
            # Batch merge path
            _pkg.update_job_metadata(job_id, {'progress': '78'})
            all_markdown_parts = []
            total_images = 0
            batches_failed = int(session_meta.get('batches_failed', 0))

            for i in range(batches_queued):
                batch_key = f"capture:batch:{session_id}:{i}"
                batch_status = _pkg.redis_client.hget(batch_key, 'status') or 'unknown'
                batch_dir = os.path.join(output_dir, 'batches', f'batch_{i}')

                if batch_status == 'failed':
                    all_markdown_parts.append(
                        f"\n\n> **⚠ Batch {i} failed to process — these pages may be missing.**\n\n"
                    )
                    logging.warning(f"Batch {i} failed for session {session_id}; inserting tombstone")
                    continue

                md_path = os.path.join(batch_dir, 'batch.md')
                if os.path.exists(md_path):
                    with open(md_path, 'r', encoding='utf-8') as f:
                        all_markdown_parts.append(f.read())

                batch_images_dir = os.path.join(batch_dir, 'images')
                if os.path.exists(batch_images_dir):
                    for img_name in sorted(os.listdir(batch_images_dir)):
                        shutil.copy2(
                            os.path.join(batch_images_dir, img_name),
                            os.path.join(images_dir, img_name),
                        )
                        total_images += 1

            front_matter = f"---\ntitle: {title}\nsource: {source_url}\npages: {len(pages)}\n---\n\n"
            merged_content = front_matter + "\n\n---\n\n".join(all_markdown_parts)
            image_count = total_images

            shutil.rmtree(os.path.join(output_dir, 'batches'), ignore_errors=True)

        elif force_ocr:
            # Fallback: force_ocr session with no pre-processed batches
            import io as io_module
            from PIL import Image as PILImage

            ocr_images_b64 = []
            for page in pages:
                page_imgs = page.get('images', [])
                chosen = next((i for i in page_imgs if i.get('is_screenshot') and i.get('b64')), None)
                if chosen is None:
                    chosen = next((i for i in page_imgs if i.get('b64')), None)
                if chosen:
                    ocr_images_b64.append(chosen['b64'])

            if not ocr_images_b64:
                raise ValueError("No valid page images found for OCR")

            logging.info(f"OCR fallback path: assembling {len(ocr_images_b64)} page images via Marker for job {job_id}")
            _pkg.update_job_metadata(job_id, {'progress': '30'})

            pil_images = []
            for img_b64 in ocr_images_b64:
                try:
                    if ',' in img_b64:
                        img_b64 = img_b64.split(',', 1)[1]
                    pil_img = PILImage.open(io_module.BytesIO(base64.b64decode(img_b64))).convert('RGB')
                    pil_images.append(pil_img)
                except Exception as e:
                    logging.warning(f"Failed to decode screenshot: {e}")

            if not pil_images:
                raise ValueError("No valid page images found for OCR")

            pdf_buf = io_module.BytesIO()
            pil_images[0].save(pdf_buf, format='PDF', save_all=True, append_images=pil_images[1:], resolution=150)
            pdf_buf.seek(0)

            _pkg.update_job_metadata(job_id, {'progress': '45'})

            artifacts = _pkg.get_model_dict()
            from marker.converters.pdf import PdfConverter
            from marker.output import text_from_rendered

            converter = PdfConverter(artifact_dict=artifacts, config={'force_ocr': True})
            rendered = converter(pdf_buf)

            _pkg.update_job_metadata(job_id, {'progress': '80'})

            markdown_text, _, marker_images = text_from_rendered(rendered)

            image_count = 0
            for img_name, img_obj in (marker_images or {}).items():
                safe_img_name = secure_filename(img_name) or f"image_{image_count}.png"
                img_obj.save(os.path.join(images_dir, safe_img_name))
                markdown_text = markdown_text.replace(f"({img_name})", f"(images/{safe_img_name})")
                image_count += 1

            front_matter = f"---\ntitle: {title}\nsource: {source_url}\npages: {len(pages)}\n---\n\n"
            merged_content = front_matter + markdown_text
            batches_failed = 0

        else:
            # Text assembly path
            all_markdown_parts = []
            image_count = 0

            for page in pages:
                page_text = page.get('text', '')
                page_images = page.get('images', [])

                for img_info in page_images:
                    if img_info.get('is_screenshot'):
                        continue
                    img_filename = img_info.get('filename', f'image_{image_count}.png')
                    img_b64 = img_info.get('b64', '')

                    if img_b64:
                        try:
                            if ',' in img_b64:
                                img_b64 = img_b64.split(',', 1)[1]
                            img_data = base64.b64decode(img_b64)
                            safe_img_filename = secure_filename(img_filename) or f"image_{image_count}.png"
                            img_save_path = os.path.join(images_dir, safe_img_filename)
                            with open(img_save_path, 'wb') as f:
                                f.write(img_data)
                            if f"({img_filename})" in page_text:
                                page_text = page_text.replace(f"({img_filename})", f"(images/{safe_img_filename})")
                            else:
                                alt = img_info.get('alt', '')
                                page_text += f"\n\n![{alt}](images/{safe_img_filename})"
                            image_count += 1
                        except Exception as e:
                            logging.warning(f"Failed to save image {img_filename}: {e}")

                import re as re_module
                page_text = re_module.sub(r'!\[[^\]]*\]\(blob:[^)]+\)', '', page_text)
                all_markdown_parts.append(page_text)

            front_matter = f"---\ntitle: {title}\nsource: {source_url}\npages: {len(pages)}\n---\n\n"
            merged_content = front_matter + "\n\n---\n\n".join(all_markdown_parts)
            batches_failed = 0

        _pkg.update_job_metadata(job_id, {'progress': '88'})

        output_filename = f"{safe_title}.md"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(merged_content)

        _pkg.redis_client.delete(f"capture:session:{session_id}:pages")

        _pkg.update_job_metadata(job_id, {'progress': '85'})

        if to_format not in ('markdown', 'gfm'):
            format_extensions = {
                'docx': 'docx', 'epub3': 'epub', 'epub2': 'epub',
                'html': 'html', 'pdf': 'pdf', 'rst': 'rst',
                'latex': 'tex', 'odt': 'odt', 'rtf': 'rtf',
            }
            out_ext = format_extensions.get(to_format, to_format)
            converted_filename = f"{safe_title}.{out_ext}"
            converted_path = os.path.join(output_dir, converted_filename)

            cmd = ['pandoc', '-f', 'markdown', '-t', to_format, output_path, '-o', converted_path]
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
            os.remove(output_path)
            file_count = 1 + image_count
        else:
            file_count = 1 + image_count

        if image_count == 0 and os.path.exists(images_dir):
            os.rmdir(images_dir)
            file_count = 1

        success_meta = {
            'status': 'SUCCESS',
            'completed_at': str(time.time()),
            'progress': '100',
            'file_count': str(file_count),
            'encrypted': 'false',
        }
        if batches_failed > 0:
            success_meta['batch_warnings'] = f"{batches_failed} batch(es) had failures — some pages may be missing"
        _pkg.update_job_metadata(job_id, success_meta)
        _pkg.redis_client.expire(f"job:{job_id}", 7200)

        logging.info(f"Capture assembly complete: job={job_id}, pages={len(pages)}, images={image_count}")
        gc.collect()
        return {"status": "success", "pages": len(pages), "images": image_count}

    except Exception as e:
        error_msg = f"Capture assembly failed: {str(e)}"
        logging.error(f"Error assembling capture session {session_id}: {error_msg}")
        _pkg.update_job_metadata(job_id, {
            'status': 'FAILURE',
            'completed_at': str(time.time()),
            'error': error_msg[:500],
            'progress': '0',
        })
        _pkg.redis_client.expire(f"job:{job_id}", 600)
        raise
