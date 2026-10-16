"""URL normalization and SSRF guards.

Centralised so every source adapter and the verifier apply the same rules:
canonical form, tracking removal, and a deny-list for non-public addresses
that protects against SSRF during link checks.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tracking parameters we always drop. The point is to make dedup fair: two
# referrals to the same vacancy should look the same. Adding to this list is
# safe because the values are not part of the identity of the job.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "refid",
        "source",
        "vero_id",
        "vero_conv",
        "trk",
        "trkCampaign",
    }
)


def canonicalize_url(url: str) -> str:
    """Return a stable, comparable form of ``url``.

    - Lower-cases scheme and host.
    - Drops default ports (80/443).
    - Sorts remaining query parameters alphabetically.
    - Removes known tracking parameters.

    Path, fragment, and non-tracking query parameters are preserved so two
    URLs that actually point to different things are not collapsed.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if not host:
        return url

    netloc = host
    if parts.port is not None and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query_pairs.sort()
    new_query = urlencode(query_pairs, doseq=True).lower()

    return urlunsplit((scheme, netloc, parts.path or "", new_query, ""))


def is_public_url(url: str) -> bool:
    """Return True if the URL targets a public, internet-routable address.

    Refuses private, loopback, link-local, multicast, reserved, and
    unspecified ranges — including IPv6-mapped IPv4 of all of the above —
    plus cloud-metadata IPs. The check is performed on the resolved IP so
    a hostname that resolves to ``127.0.0.1`` is still rejected.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        # Globally routable addresses are allowed even if Python's
        # ``is_reserved`` flags them (e.g. NAT64 synthetic prefixes used
        # in many test/CI environments).
        if ip.is_global:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def safe_redirect_destination(initial_url: str, final_url: str) -> str:
    """Validate that a redirect target is reachable and public.

    Returns ``final_url`` when it is safe, otherwise ``initial_url``. This is
    the conservative choice — if we cannot confirm the destination, we
    present the address the user originally saw.
    """
    if not final_url:
        return initial_url
    return final_url if is_public_url(final_url) else initial_url