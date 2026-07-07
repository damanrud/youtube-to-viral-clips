import os
import json
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from flask import session
from pathlib import Path

CLIENT_SECRET_FILE = Path(__file__).parent.parent / 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def get_oauth_flow():
    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE),
        scopes=SCOPES,
        redirect_uri='http://localhost:5000/api/youtube/callback'
    )
    return flow

def get_credentials_from_session():
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
    return None

def save_credentials_to_session(credentials):
    session['youtube_token'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

def refresh_credentials_if_needed(credentials):
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_credentials_to_session(credentials)
        return True
    return False