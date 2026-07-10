import os
import json
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from flask import session
from pathlib import Path

CLIENT_SECRET_FILE = Path(__file__).parent.parent / 'client_secret.json'
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]
TOKEN_FILE = Path(__file__).parent.parent / 'token.json'

# Глобальный кеш токена (доступен вне запроса)
_cached_creds = None

def get_oauth_flow():
    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE),
        scopes=SCOPES,
        redirect_uri='http://localhost:5000/api/youtube/callback'
    )
    return flow

def get_credentials_from_session():
    """Возвращает токен из сессии, а если там нет – из глобального кеша."""
    if 'youtube_token' in session:
        token_data = session['youtube_token']
        return Credentials(
            token=token_data.get('token'),
            refresh_token=token_data.get('refresh_token'),
            token_uri=token_data.get('token_uri'),
            client_id=token_data.get('client_id'),
            client_secret=token_data.get('client_secret'),
            scopes=token_data.get('scopes', SCOPES)
        )
    # Если в сессии нет, возвращаем закешированный токен (из файла)
    return get_cached_creds()

def save_credentials_to_session(credentials):
    """Сохраняет токен в сессию, в файл и в глобальный кеш."""
    session['youtube_token'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    save_token_to_file(credentials)
    set_cached_creds(credentials)

def refresh_credentials_if_needed(credentials):
    """Обновляет токен, если он истёк, и сохраняет во все хранилища."""
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_credentials_to_session(credentials)
        return True
    return False

def save_token_to_file(credentials):
    """Сохраняет токен в файл token.json."""
    with open(TOKEN_FILE, 'w') as f:
        json.dump({
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }, f)
    set_cached_creds(credentials)

def load_token_from_file():
    """Загружает токен из файла и кеширует его глобально."""
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            creds = Credentials(
                token=data.get('token'),
                refresh_token=data.get('refresh_token'),
                token_uri=data.get('token_uri'),
                client_id=data.get('client_id'),
                client_secret=data.get('client_secret'),
                scopes=data.get('scopes', SCOPES)
            )
            set_cached_creds(creds)
            return creds
        except Exception:
            return None
    return None

def set_cached_creds(credentials):
    global _cached_creds
    _cached_creds = credentials

def get_cached_creds():
    return _cached_creds