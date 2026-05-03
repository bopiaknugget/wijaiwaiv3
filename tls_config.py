"""TLS certificate environment hygiene for local Windows installs.

Some local tools set process-wide CA bundle variables to files that may not
exist in this app's runtime. Requests-compatible libraries then fail before any
network call is made. Keep valid custom CA bundles, but discard broken paths so
libraries can fall back to their packaged certifi bundle.
"""

from __future__ import annotations

import os
from pathlib import Path


CA_BUNDLE_ENV_VARS = (
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
)
CA_DIR_ENV_VARS = ("SSL_CERT_DIR",)


def _has_existing_path(value: str, expect_dir: bool = False) -> bool:
    if not value:
        return False
    path = Path(value)
    return path.is_dir() if expect_dir else path.is_file()


def sanitize_tls_ca_bundle_env() -> dict[str, str]:
    """Remove invalid process-wide TLS CA bundle env vars.

    Returns a mapping of removed variable names to their invalid values. The
    return value is useful for tests or diagnostics; callers normally ignore it.
    """
    removed = {}

    for name in CA_BUNDLE_ENV_VARS:
        value = os.environ.get(name)
        if value and not _has_existing_path(value):
            removed[name] = value
            os.environ.pop(name, None)

    for name in CA_DIR_ENV_VARS:
        value = os.environ.get(name)
        if value and not _has_existing_path(value, expect_dir=True):
            removed[name] = value
            os.environ.pop(name, None)

    return removed
