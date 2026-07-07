import os
import json
from pathlib import Path
from datetime import datetime

def save_metadata(video_path, metadata):
    """Сохраняет метаданные в .txt файл рядом с видео"""
    video_path = Path(video_path)
    txt_path = video_path.with_suffix('.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(f"Title: {metadata.get('title', '')}\n")
        f.write(f"Description: {metadata.get('description', '')}\n")
        f.write(f"Tags: {', '.join(metadata.get('tags', []))}\n")
        f.write(f"Platform: {metadata.get('platform', '')}\n")
        f.write(f"Created: {datetime.now().isoformat()}\n")
    return str(txt_path)

def upload_to_youtube(video_path, title, description, tags, privacy_status='private', thumbnail_path=None):
    """
    Загружает видео на YouTube.
    privacy_status: 'private' (черновик), 'unlisted', 'public'
    Требуется наличие token_youtube.json в корне проекта.
    """
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        raise ImportError("Google API client not installed. Run: pip install google-api-python-client google-auth-oauthlib")

    creds = Credentials.from_authorized_user_file('token_youtube.json')
    youtube = build('youtube', 'v3', credentials=creds)

    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': '22'
        },
        'status': {
            'privacyStatus': privacy_status
        }
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part='snippet,status',
        body=body,
        media_body=media
    )
    response = request.execute()
    return response

def upload_to_tiktok(video_path, title, description, privacy='public'):
    """
    Заглушка для TikTok – в реальности нужно использовать API или библиотеку.
    Установите: pip install tiktok-uploader
    """
    # from tiktok_uploader.upload import upload_video
    # upload_video(video_path, description=description, privacy=privacy)
    print(f"[TikTok] Видео {video_path} готово к загрузке. Описание: {description}")
    return {"status": "mock_upload"}