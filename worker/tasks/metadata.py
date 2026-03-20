"""
Metadata tasks: SLM extraction and Amazon session testing.
"""

import os
import time
import json
import logging
from urllib.parse import urlparse

import tasks as _pkg


@_pkg.celery.task(
    name='tasks.extract_slm_metadata',
    time_limit=300,
    soft_time_limit=240,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=1
)
def extract_slm_metadata(job_id, markdown_file_path):
    """Extracts semantic metadata (title, tags, summary) from Markdown content using a local SLM."""
    from warmup import get_slm_model

    logging.info(f"Starting SLM metadata extraction for job {job_id} from {markdown_file_path}")
    _pkg.update_job_metadata(job_id, {'slm_status': 'PROCESSING', 'slm_started_at': str(time.time())})

    try:
        slm = get_slm_model()
        if slm is None:
            logging.warning(f"SLM model not loaded for job {job_id}. Skipping metadata extraction.")
            _pkg.update_job_metadata(job_id, {'slm_status': 'SKIPPED', 'slm_error': 'SLM model not available'})
            return {"status": "skipped", "message": "SLM model not available"}

        if not os.path.exists(markdown_file_path):
            logging.error(f"Markdown file not found for SLM extraction: {markdown_file_path}")
            _pkg.update_job_metadata(job_id, {'slm_status': 'FAILURE', 'slm_error': 'Markdown file missing'})
            return {"status": "failure", "message": "Markdown file missing"}

        with open(markdown_file_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()

        MAX_SLM_CONTEXT = _pkg.app_settings.max_slm_context
        if len(markdown_content.split()) > MAX_SLM_CONTEXT:
            markdown_content = " ".join(markdown_content.split()[:MAX_SLM_CONTEXT])
            logging.warning(f"Truncated markdown content for SLM inference for job {job_id}")

        prompt = (
            "You are a helpful assistant that extracts structured information from documents. "
            "Given the following Markdown content, extract a concise title, relevant tags (up to 5), "
            "and a brief summary (1-2 sentences). "
            "Respond ONLY with a JSON object. Ensure the output is valid JSON.\n\n"
            "Markdown Content:\n"
            f"{markdown_content}\n\n"
            "JSON Output Structure:\n"
            "```json\n"
            "{\n"
            '  "title": "Concise document title",\n'
            '  "tags": ["tag1", "tag2"],\n'
            '  "summary": "Brief summary of the document."\n'
            "}\n"
            "```\n"
            "JSON Output:\n"
        )

        logging.info(f"Sending prompt to SLM for job {job_id}...")

        output = slm.create_completion(
            prompt,
            max_tokens=512,
            temperature=0.1,
            top_p=0.9,
            stop=["```"],
        )

        generated_text = output['choices'][0]['text'].strip()
        logging.info(f"SLM generated raw text for job {job_id}:\n{generated_text}")

        json_start = generated_text.find('{')
        json_end = generated_text.rfind('}')
        if json_start != -1 and json_end != -1:
            json_str = generated_text[json_start:json_end+1]
        else:
            raise ValueError("No valid JSON found in SLM output.")

        metadata = json.loads(json_str)

        if not all(k in metadata for k in ["title", "tags", "summary"]):
            raise ValueError("Invalid metadata structure returned by SLM.")
        if not isinstance(metadata["tags"], list):
            metadata["tags"] = [str(metadata["tags"])]

        _pkg.update_job_metadata(job_id, {
            'slm_status': 'SUCCESS',
            'slm_completed_at': str(time.time()),
            'slm_title': metadata.get('title', ''),
            'slm_tags': json.dumps(metadata.get('tags', [])),
            'slm_summary': metadata.get('summary', '')
        })
        logging.info(f"SLM metadata extracted and stored for job {job_id}")
        return {"status": "success", "metadata": metadata}

    except Exception as e:
        error_msg = f"SLM metadata extraction failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        _pkg.update_job_metadata(job_id, {'slm_status': 'FAILURE', 'slm_error': error_msg})
        raise


@_pkg.celery.task(
    name='tasks.test_amazon_session',
    time_limit=120,
    soft_time_limit=90,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0
)
def test_amazon_session(job_id, encrypted_session_file_path):
    """Tests an Amazon session by launching Playwright with the provided session state."""
    from encryption import EncryptionService
    from key_manager import create_key_manager

    logging.info(f"Starting Amazon session test for job {job_id}")
    _pkg.update_job_metadata(job_id, {'amazon_session_status': 'TESTING', 'amazon_session_started_at': str(time.time())})

    decrypted_session_file = None
    try:
        encryption_service = EncryptionService()
        key_manager = create_key_manager(_pkg.redis_client)

        session_dek = key_manager.get_job_key(job_id)
        if not session_dek:
            raise ValueError(f"No decryption key found for job {job_id}")

        decrypted_session_file = encrypted_session_file_path.replace(".enc", ".json")
        encryption_service.decrypt_file(
            input_path=encrypted_session_file_path,
            output_path=decrypted_session_file,
            key=session_dek,
            associated_data=job_id
        )

        with open(decrypted_session_file, 'r', encoding='utf-8') as f:
            storage_state_json = json.load(f)

        logging.info(f"Decrypted session file for job {job_id}. Calling MCP server...")

        target_url = "https://read.amazon.com/kp/notebook"
        mcp_response = _pkg.call_mcp_server(
            'create_context_and_goto',
            {'url': target_url, 'storageState': storage_state_json}
        )

        if mcp_response.get('success'):
            final_url = mcp_response.get('url', '')
            parsed_url = urlparse(final_url)
            hostname = parsed_url.hostname

            if hostname and (
                hostname in ('signin.amazon.com', 'kindle.amazon.com') or
                hostname.endswith('.signin.amazon.com') or
                hostname.endswith('.kindle.amazon.com')
            ):
                logging.warning(f"Amazon session for job {job_id} is invalid: redirected to {hostname}.")
                _pkg.update_job_metadata(job_id, {
                    'amazon_session_status': 'INVALID',
                    'amazon_session_completed_at': str(time.time()),
                    'amazon_session_error': f'Redirected to {hostname}'
                })
                return {"status": "invalid", "message": f"Session invalid: redirected to {hostname}."}
            else:
                logging.info(f"Amazon session for job {job_id} is VALID: successfully accessed {target_url}.")
                _pkg.update_job_metadata(job_id, {
                    'amazon_session_status': 'VALID',
                    'amazon_session_completed_at': str(time.time()),
                    'amazon_session_error': ''
                })
                return {"status": "valid", "message": "Session is valid."}
        else:
            error_msg = mcp_response.get('error', 'Unknown MCP error')
            logging.error(f"MCP server failed for job {job_id}: {error_msg}")
            _pkg.update_job_metadata(job_id, {
                'amazon_session_status': 'FAILURE',
                'amazon_session_completed_at': str(time.time()),
                'amazon_session_error': f"MCP server error: {error_msg}"
            })
            return {"status": "failure", "message": f"MCP server error: {error_msg}"}

    except Exception as e:
        error_msg = f"Amazon session test failed for job {job_id}: {str(e)}"
        logging.error(error_msg)
        _pkg.update_job_metadata(job_id, {'amazon_session_status': 'FAILURE', 'amazon_session_error': error_msg})
        raise
    finally:
        if os.path.exists(encrypted_session_file_path):
            os.remove(encrypted_session_file_path)
            logging.info(f"Purged encrypted session file: {encrypted_session_file_path}")
        if decrypted_session_file and os.path.exists(decrypted_session_file):
            os.remove(decrypted_session_file)
            logging.info(f"Purged decrypted session file: {decrypted_session_file}")
        key_manager.delete_job_key(job_id)
        logging.info(f"Purged session key for job {job_id}")
