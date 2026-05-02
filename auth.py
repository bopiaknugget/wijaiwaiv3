"""
Google OAuth 2.0 Authentication Module for WijaiWai
Handles Google Sign-In flow: authorization URL generation, token exchange,
user profile retrieval, and callback handling.
"""

import os
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

import database

# Load environment variables
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501/oauth2callback")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Scopes needed: basic profile info and email
SCOPES = "openid email profile"


def get_google_auth_url() -> str:
    """
    Build and return the Google OAuth 2.0 authorization URL.
    The user will be redirected to this URL to sign in with Google.

    Returns:
        str: Full Google OAuth authorization URL with query parameters
    """
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    """
    Exchange an authorization code for an access token.

    Args:
        code: The authorization code returned by Google after user consent

    Returns:
        dict: Token response containing access_token, token_type, etc.

    Raises:
        ValueError: If the token exchange fails
    """
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    response = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=30)

    if response.status_code != 200:
        raise ValueError(
            f"Token exchange failed (HTTP {response.status_code}): "
            f"{response.text}"
        )

    return response.json()


def get_user_info(access_token: str) -> dict:
    """
    Fetch the authenticated user's profile from Google API.

    Args:
        access_token: Valid Google OAuth access token

    Returns:
        dict: User profile with keys: id, email, name, picture
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=30)

    if response.status_code != 200:
        raise ValueError(
            f"Failed to fetch user info (HTTP {response.status_code}): "
            f"{response.text}"
        )

    data = response.json()
    return {
        "id": f"google_{data.get('id', '')}",
        "email": data.get("email", ""),
        "name": data.get("name", ""),
        "picture": data.get("picture", ""),
    }


def handle_oauth_callback(code: str) -> dict:
    """
    Complete the OAuth 2.0 flow: exchange code -> get user info -> save to DB.

    Args:
        code: Authorization code from Google (extracted from URL query params)

    Returns:
        dict: User info dict with keys: id, email, name, picture

    Raises:
        ValueError: If any step of the OAuth flow fails
    """
    # Step 1: Exchange code for access token
    token_data = exchange_code_for_token(code)
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("No access_token in token response")

    # Step 2: Fetch user profile
    user_info = get_user_info(access_token)

    # Step 3: Save/update user in SQLite
    database.save_user(user_info)

    return user_info
