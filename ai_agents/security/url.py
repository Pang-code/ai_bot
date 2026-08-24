import ipaddress
import socket
from urllib.parse import urlsplit


def is_safe_public_url(url: str, *, resolve_dns: bool = False) -> bool:
    """Allow only credential-free HTTP(S) URLs targeting public addresses."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return False
        try:
            addresses = [ipaddress.ip_address(hostname)]
        except ValueError:
            if not resolve_dns:
                return True
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, parsed.port or 443)
            ]
        return bool(addresses) and all(address.is_global for address in addresses)
    except (ValueError, OSError):
        return False
