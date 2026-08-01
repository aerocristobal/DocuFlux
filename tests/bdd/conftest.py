"""Wiring for the executable capability specs.

Step definitions live in tests/features/steps/ next to the features they describe;
this star-imports them so pytest can find them. pytest-bdd's given/when/then decorators
create fixtures, and fixtures are discovered from a conftest regardless of where the
function was defined — the same trick tests/unit/conftest.py already uses.

Deliberately not `pytest_plugins`: pytest >= 7 errors on that outside the rootdir
conftest, and the rootdir here is the project root where pytest.ini lives, not tests/.

Import discipline for anything added here. `pythonpath = . web worker shared` makes
several modules importable under two names, and two import paths for one file produce
two distinct module objects — so `patch('validation.x')` and `patch('web.validation.x')`
patch different things. Reach web code as `web.*`, worker code as `tasks.*`, and shared
code flat (`storage`, `quality`, `job_metadata`), matching how production imports them.
Never patch `'app.…'` in new code; the alias in tests/conftest.py exists for the 640
tests that predate this.
"""
from tests.support.fixtures import (  # noqa: F401 — re-exported for the step definitions
    isolated_client,
    mock_redis,
    mock_celery,
    mock_disk_space,
    api_headers,
    admin_headers,
)

from tests.features.steps.common_steps import *  # noqa: F401,F403
from tests.features.steps.conversion_steps import *  # noqa: F401,F403
from tests.features.steps.capture_steps import *  # noqa: F401,F403
from tests.features.steps.api_v1_steps import *  # noqa: F401,F403
from tests.features.steps.auth_steps import *  # noqa: F401,F403
from tests.features.steps.security_steps import *  # noqa: F401,F403
from tests.features.steps.reliability_steps import *  # noqa: F401,F403
