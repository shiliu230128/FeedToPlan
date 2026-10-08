"""
net.py
======
Shared HTTPS configuration.

python.org's macOS installer does not ship a CA bundle until you run
``/Applications/Python X.Y/Install Certificates.command``. Until then every
HTTPS request fails with::

    SSL: CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate

and, because the crawler retries ``URLError`` with backoff, one missing bundle
turns into minutes of silent stalling instead of an obvious error.

Resolve a usable bundle ourselves: prefer ``certifi`` (installed by the official
certificates command, and by most toolchains), then the well-known platform
bundles, and finally fall back to whatever Python has configured.
"""
from __future__ import annotations

import ssl
from pathlib import Path
from typing import Optional

# Platform CA bundles, in the order worth trying.
_SYSTEM_CA_CANDIDATES = (
    "/etc/ssl/cert.pem",                        # macOS system bundle
    "/usr/local/etc/openssl/cert.pem",          # Homebrew (Intel prefix)
    "/opt/homebrew/etc/openssl@3/cert.pem",     # Homebrew (Apple Silicon)
    "/opt/homebrew/etc/openssl/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",         # RHEL / CentOS
    "/etc/ssl/certs/ca-certificates.crt",       # Debian / Ubuntu
)

_ca_bundle: Optional[str] = None
_checked = False


def ca_bundle() -> Optional[str]:
    """Path to a usable CA bundle, or None to leave Python's default alone."""
    global _ca_bundle, _checked
    if _checked:
        return _ca_bundle
    _checked = True
    try:
        import certifi  # type: ignore

        path = certifi.where()
        if path and Path(path).is_file():
            _ca_bundle = path
            return _ca_bundle
    except Exception:
        pass
    for candidate in _SYSTEM_CA_CANDIDATES:
        if Path(candidate).is_file():
            _ca_bundle = candidate
            return _ca_bundle
    return None


def ssl_context() -> ssl.SSLContext:
    """SSL context for outgoing HTTPS requests, with a resolvable CA bundle."""
    bundle = ca_bundle()
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


def describe() -> str:
    """One-line summary of the resolved CA source, for diagnostics."""
    bundle = ca_bundle()
    if not bundle:
        return "CA bundle: Python default (certifi and platform bundles all missing)"
    return f"CA bundle: {bundle}"
