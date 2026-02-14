"""
Secrets Management for DocuFlux Web Service

Provides secure secrets loading from multiple sources with fallback priority:
1. Docker Swarm secrets (/run/secrets/)
2. Environment variables
3. Default values (rejected in production)

Epic 21.7: Secrets Management and Rotation
"""

import os
import logging
from pathlib import Path
import base64
import binascii
from typing import Dict, Any, Optional

import os
import logging
from pathlib import Path
import base64
import binascii
from typing import Dict, Any, Optional

def load_secret(name: str, default: Optional[str] = None, required: bool = False, reject_default_in_prod: bool = True) -> Optional[str]:
    """
    Load a secret from multiple sources with priority order.

    Priority:
    1. Docker Swarm secret file (/run/secrets/<name>)
    2. Environment variable
    3. Default value (if provided)

    Args:
        name: Secret name (e.g., 'secret_key', 'database_password')
        default: Default value if secret not found
        required: If True, raise exception if secret not found
        reject_default_in_prod: If True, fail if using default in production

    Returns:
        Secret value as string

    Raises:
        ValueError: If required secret not found or default used in production
    """
    # 1. Try Docker Swarm secrets
    secret_path = Path(f'/run/secrets/{name}')
    if secret_path.exists():
        try:
            with open(secret_path, 'r') as f:
                value = f.read().strip()
                if value:
                    logging.info(f"Loaded secret '{name}' from Docker Swarm secrets")
                    return value
        except Exception as e:
            logging.warning(f"Failed to read secret from {secret_path}: {e}")

    # 2. Try environment variable
    env_var_name = name.upper()
    value = os.environ.get(env_var_name)
    if value:
        logging.info(f"Loaded secret '{name}' from environment variable {env_var_name}")
        return value

    # 3. Use default value (with production check)
    if default is not None:
        is_production = os.environ.get('FLASK_ENV', 'production') == 'production'
        is_default_insecure = default in ['change-me-in-production', 'insecure-default', 'dev-only']

        if reject_default_in_prod and is_production and is_default_insecure:
            error_msg = (
                f"SECURITY ERROR: Secret '{name}' is using default value in production environment. "
                f"This is a critical security vulnerability. "
                f"Please set {env_var_name} environment variable or use Docker secrets."
            )
            logging.error(error_msg)
            raise ValueError(error_msg)

        if is_default_insecure:
            logging.warning(
                f"WARNING: Secret '{name}' is using insecure default value. "
                f"Set {env_var_name} for production use."
            )

        return default

    # 4. Secret not found and no default
    if required:
        error_msg = (
            f"Required secret '{name}' not found. "
            f"Provide via Docker secret (/run/secrets/{name}) or environment variable {env_var_name}."
        )
        logging.error(error_msg)
        raise ValueError(error_msg)

    logging.warning(f"Secret '{name}' not found, returning None")
    return None


def generate_master_encryption_key() -> str:
    """
    Generate a new master encryption key for development use.

    Epic 23.5: Auto-generate key in development if not provided

    Returns:
        Base64 URL-safe encoded 256-bit key
    """
    key_bytes = os.urandom(32)  # 256 bits
    return base64.urlsafe_b64encode(key_bytes).decode('utf-8')


def load_all_secrets() -> Dict[str, Any]:
    """
    Load all application secrets required by DocuFlux.

    Returns:
        Dictionary of secret names to values
    """
    secrets: Dict[str, Any] = {}

    # Flask secret key
    secrets['SECRET_KEY'] = load_secret(
        'secret_key',
        default=os.environ.get('SECRET_KEY', 'change-me-in-production'),
        required=True,
        reject_default_in_prod=True
    )

    # Cloudflare Tunnel token (optional)
    secrets['CLOUDFLARE_TUNNEL_TOKEN'] = load_secret(
        'cloudflare_tunnel_token',
        default=os.environ.get('CLOUDFLARE_TUNNEL_TOKEN'),
        required=False,
        reject_default_in_prod=False
    )

    # Epic 23.5: Master encryption key
    # Auto-generate in development, require explicit key in production
    is_production = os.environ.get('FLASK_ENV', 'production') == 'production'

    master_key_default = None
    if not is_production:
        # Development: Generate ephemeral key if none provided
        master_key_default = generate_master_encryption_key()
        logging.warning(
            "Generated ephemeral master encryption key for development. "
            "Set MASTER_ENCRYPTION_KEY environment variable for persistent encryption."
        )

    secrets['MASTER_ENCRYPTION_KEY'] = load_secret(
        'master_encryption_key',
        default=master_key_default,
        required=is_production,  # Required in production
        reject_default_in_prod=True
    )

    # Epic 24.2: Celery signing key for task message authentication
    celery_key_default = None
    if not is_production:
        # Development: Generate ephemeral signing key
        celery_key_default = binascii.hexlify(os.urandom(32)).decode('ascii')
        logging.warning(
            "Generated ephemeral Celery signing key for development. "
            "Set CELERY_SIGNING_KEY environment variable for persistent signing."
        )

    secrets['CELERY_SIGNING_KEY'] = load_secret(
        'celery_signing_key',
        default=celery_key_default,
        required=is_production,  # Required in production
        reject_default_in_prod=True
    )

    return secrets
