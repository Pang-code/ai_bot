from ai_agents.security.url import is_safe_public_url


def test_allows_public_http_urls() -> None:
    assert is_safe_public_url("https://example.com/path?q=1")
    assert is_safe_public_url("http://8.8.8.8/")


def test_rejects_local_private_and_credential_urls() -> None:
    blocked = [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "https://user:password@example.com/",
        "javascript:alert(1)",
    ]
    assert all(not is_safe_public_url(url) for url in blocked)
