import uuid


def validate_uuid(value):
    """Return True if value is a valid UUID string."""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False
