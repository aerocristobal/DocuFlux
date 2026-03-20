"""
Blueprint registration for DocuFlux web routes.

Imports are inside the function to avoid circular imports — blueprints
reference web.app module globals, which must be fully initialized first.
"""


def register_blueprints(app):
    """Register all route blueprints with the Flask app."""
    from web.routes.health import health_bp
    from web.routes.conversion import conversion_bp
    from web.routes.auth import auth_bp
    from web.routes.capture import capture_bp
    from web.routes.webhooks import webhooks_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(conversion_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(capture_bp)
    app.register_blueprint(webhooks_bp)
