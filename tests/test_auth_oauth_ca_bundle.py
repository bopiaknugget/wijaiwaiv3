from unittest.mock import Mock, patch

import auth


def test_exchange_code_for_token_uses_requests_ca_bundle():
    response = Mock(status_code=200)
    response.json.return_value = {"access_token": "token"}

    with patch("auth.requests.post", return_value=response) as post:
        assert auth.exchange_code_for_token("code") == {"access_token": "token"}

    _, kwargs = post.call_args
    assert kwargs["verify"] == auth.GOOGLE_OAUTH_CA_BUNDLE
    assert kwargs["verify"]


def test_get_user_info_uses_requests_ca_bundle():
    response = Mock(status_code=200)
    response.json.return_value = {
        "id": "123",
        "email": "user@example.com",
        "name": "User",
        "picture": "https://example.com/pic.png",
    }

    with patch("auth.requests.get", return_value=response) as get:
        user = auth.get_user_info("token")

    _, kwargs = get.call_args
    assert kwargs["verify"] == auth.GOOGLE_OAUTH_CA_BUNDLE
    assert kwargs["verify"]
    assert user["id"] == "google_123"
