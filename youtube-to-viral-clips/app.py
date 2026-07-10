import json
import queue
import threading
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import secrets
import base64
import logging
import shutil
import flask
import time
import httplib2
import sys
import io
import requests

# Исправление кодировки для Windows-консоли
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from flask import Flask, render_template, request, jsonify, Response, send_from_directory, session, redirect, url_for
import os

# ---- ЗАГРУЗКА .env ----
from dotenv import load_dotenv

load_dotenv()

# ---- Логирование ----
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---- Импорт helpers ----
from utils.helpers import (
    check_dependencies,
    clean_filename,
    set_ffmpeg_env,
    get_ffmpeg_path
)

try:
    ffmpeg_path = set_ffmpeg_env()
    logger.info(f"FFmpeg используется: {ffmpeg_path}")
except RuntimeError as e:
    logger.error(str(e))

# ---- Импорт модулей ----
from modules.downloader import YouTubeDownloader
from modules.transcriber import VideoTranscriber
from modules.analyzer import ViralMomentAnalyzer
from modules.video_processor import VideoProcessor
from modules.subtitle_generator import SubtitleGenerator
from modules.social_publisher import save_metadata, upload_to_youtube, upload_to_tiktok
from modules.stream_processor import StreamProcessor
from modules.youtube_auth import (
    get_oauth_flow, get_credentials_from_session,
    save_credentials_to_session, refresh_credentials_if_needed,
    load_token_from_file, save_token_to_file,
    set_cached_creds
)
from config import (
    SUBTITLE_TEMPLATES, OPENAI_API_KEY, ANTHROPIC_API_KEY,
    DOWNLOADS_DIR, OUTPUTS_DIR, SUPPORTED_FORMATS, CHUNK_DURATION,
    MIN_VIRAL_SCORE
)
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
from google_auth_httplib2 import AuthorizedHttp

app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))
if not os.getenv("FLASK_SECRET_KEY"):
    logger.warning("FLASK_SECRET_KEY не задан в .env – сессии будут сбрасываться при перезапуске!")

# ---- ПРИ СТАРТЕ ЗАГРУЖАЕМ ТОКЕН ИЗ ФАЙЛА В ГЛОБАЛЬНЫЙ КЕШ ----
creds = load_token_from_file()
if creds and not creds.expired:
    logger.info("Загружен сохранённый токен YouTube")
elif creds and creds.expired:
    logger.warning("Токен YouTube истёк, будет обновлён при первом запросе")
else:
    logger.info("Токен YouTube не найден, требуется авторизация")

_sessions: dict = {}


# ---- PKCE helpers ----
def generate_code_verifier():
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
    code_verifier = code_verifier.replace('=', '').replace('+', '-').replace('/', '_')
    return code_verifier


def generate_code_challenge(code_verifier):
    sha256_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(sha256_hash).decode('utf-8').replace('=', '')
    return code_challenge


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stream")
def stream_page():
    return render_template("stream.html")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
@app.route("/api/subtitle-styles", methods=["GET"])
def subtitle_styles():
    styles = {name: tmpl["description"] for name, tmpl in SUBTITLE_TEMPLATES.items()}
    return jsonify(styles)


@app.route("/api/downloads", methods=["GET"])
def list_downloads():
    downloads_path = Path(DOWNLOADS_DIR)
    downloads_path.mkdir(exist_ok=True)
    files = [
        f.name for f in downloads_path.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_FORMATS
    ]
    return jsonify(files)


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400
    downloads_path = Path(DOWNLOADS_DIR)
    downloads_path.mkdir(exist_ok=True)
    filename = clean_filename(file.filename)
    save_path = downloads_path / filename
    file.save(str(save_path))
    return jsonify({"filename": filename})


@app.route("/api/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(str(OUTPUTS_DIR), filename)


@app.route("/api/banners")
def get_banners():
    banner_dir = Path('static/banners')
    banner_dir.mkdir(exist_ok=True)
    banners = [f.name for f in banner_dir.iterdir() if f.is_file()]
    return jsonify(banners)


# ===================== YOUTUBE OAuth =====================
@app.route("/api/youtube/auth")
def youtube_auth():
    flow = get_oauth_flow()
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        code_challenge_method='S256',
        code_challenge=code_challenge,
    )
    _sessions[state] = {'code_verifier': code_verifier}
    logger.info(f"[AUTH] Сохранили code_verifier для state {state[:8]}")
    return redirect(authorization_url)


@app.route("/api/youtube/callback")
def youtube_callback():
    flow = get_oauth_flow()
    state = request.args.get('state')
    if not state:
        return jsonify({'error': 'Missing state parameter'}), 400

    session_data = _sessions.pop(state, None)
    if not session_data:
        return jsonify({'error': 'Invalid state or session expired'}), 400

    code_verifier = session_data.get('code_verifier')
    if not code_verifier:
        return jsonify({'error': 'Missing code verifier'}), 400

    logger.info(f"[CALLBACK] Используем code_verifier для state {state[:8]}")
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    flow.fetch_token(
        authorization_response=request.url,
        code_verifier=code_verifier
    )
    credentials = flow.credentials
    save_credentials_to_session(credentials)
    return redirect(url_for('index'))


@app.route("/api/youtube/status", methods=["GET"])
def youtube_status():
    creds = get_credentials_from_session()
    if creds and not creds.expired:
        return jsonify({'authenticated': True})
    return jsonify({'authenticated': False})


@app.route("/api/youtube/logout", methods=["POST"])
def youtube_logout():
    session.pop('youtube_token', None)
    token_file = Path('token.json')
    if token_file.exists():
        token_file.unlink()
    set_cached_creds(None)
    return jsonify({'status': 'ok'})


# ===================== ЭНДПОИНТ ДЛЯ ПОЛУЧЕНИЯ МОДЕЛЕЙ OLLAMA =====================
@app.route("/api/ollama/models", methods=["GET"])
def ollama_models():
    """Возвращает список доступных моделей Ollama."""
    try:
        import ollama
        # В новых версиях ollama.list() возвращает словарь с ключом 'models'
        # или список моделей напрямую. Пробуем оба варианта.
        response = ollama.list()
        if isinstance(response, dict) and 'models' in response:
            models = response['models']
            model_names = [model.get('name', '') for model in models if model.get('name')]
        elif isinstance(response, list):
            model_names = [model.get('name', '') for model in response if model.get('name')]
        else:
            # fallback
            model_names = []
        # Фильтруем пустые имена
        model_names = [name for name in model_names if name]
        logger.info(f"Получены модели Ollama: {model_names}")
        return jsonify({'models': model_names})
    except Exception as e:
        logger.error(f"Failed to get Ollama models: {e}")
        return jsonify({'models': []})


# ===================== ПУБЛИКАЦИЯ =====================
@app.route("/api/publish", methods=["POST"])
def publish_video():
    start_time = time.time()
    logger.info("=== НАЧАЛО ПУБЛИКАЦИИ ===")

    payload = flask.request.get_json(silent=True) or {}
    logger.info(f"publish_video received: {payload}")

    video_path = payload.get('video_path')
    title = payload.get('title', '')
    description = payload.get('description', '')
    tags = payload.get('tags', [])
    privacy = payload.get('privacy', 'private')
    save_only = payload.get('save_only', False)

    if not video_path:
        logger.error("Missing video_path")
        return jsonify({'error': 'Missing video_path'}), 400

    path_obj = Path(video_path)
    if not path_obj.exists():
        logger.error(f"Video file not found: {video_path}")
        return jsonify({'error': f'Video file not found: {video_path}'}), 400

    file_size_mb = path_obj.stat().st_size / (1024 * 1024)
    logger.info(f"Видео существует: {video_path}, размер: {file_size_mb:.2f} MB")

    txt_path = save_metadata(video_path, {
        'title': title,
        'description': description,
        'tags': tags,
        'platform': 'youtube'
    })
    logger.info(f"Метаданные сохранены в {txt_path}")

    if save_only:
        return jsonify({'status': 'ok', 'txt_path': txt_path})

    creds = get_credentials_from_session()
    if not creds:
        logger.error("Нет токена в сессии")
        return jsonify({'error': 'Not authenticated with YouTube. Please log in.'}), 401

    if creds.expired:
        logger.info("Токен истёк, пробуем обновить...")
        if not refresh_credentials_if_needed(creds):
            logger.error("Не удалось обновить токен")
            return jsonify({'error': 'Credentials expired. Please re-authenticate.'}), 401
        logger.info("Токен успешно обновлён")

    try:
        http = httplib2.Http(timeout=600)
        http.follow_redirects = False
        http = AuthorizedHttp(creds, http=http)

        logger.info("Создаём клиент YouTube API с увеличенным таймаутом (600 сек)...")
        youtube = build('youtube', 'v3', http=http)

        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22'
            },
            'status': {'privacyStatus': privacy}
        }

        CHUNK_SIZE = 512 * 1024
        logger.info(f"Создаём MediaFileUpload с чанками {CHUNK_SIZE / 1024:.0f} КБ...")
        media = MediaFileUpload(video_path, chunksize=CHUNK_SIZE, resumable=True)

        logger.info("Выполняем запрос videos().insert()...")
        upload_request = youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )

        max_retries = 3
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка загрузки #{attempt + 1}...")
                response = upload_request.execute()
                elapsed = time.time() - start_time
                video_id = response.get('id')
                logger.info(f"Загрузка завершена за {elapsed:.1f} сек. ID видео: {video_id}")
                logger.info(f"Ссылка на видео: https://www.youtube.com/watch?v={video_id}")
                return jsonify({
                    'status': 'ok',
                    'result': response,
                    'txt_path': txt_path,
                    'video_id': video_id,
                    'video_url': f"https://www.youtube.com/watch?v={video_id}"
                })
            except (HttpError, ConnectionResetError, httplib2.ServerNotFoundError,
                    httplib2.RedirectMissingLocation) as e:
                if attempt < max_retries - 1:
                    wait = retry_delay * (2 ** attempt)
                    logger.warning(f"Попытка #{attempt + 1} не удалась: {str(e)}. Повтор через {wait} сек...")
                    time.sleep(wait)
                else:
                    raise

    except Exception as e:
        elapsed = time.time() - start_time
        logger.exception(f"Ошибка при загрузке на YouTube (через {elapsed:.1f} сек): {str(e)}")
        return jsonify({'error': str(e)}), 500


# ===================== СТРИМ-РЕЖИМ (с поддержкой AI-настроек) =====================
@app.route("/api/process_stream", methods=["POST"])
def process_stream():
    payload = request.get_json(silent=True) or {}
    source = payload.get('source')
    local_file = payload.get('local_file')
    streamer_name = payload.get('streamer_name')

    # ---- Принимаем AI-параметры ----
    ai_provider = payload.get('provider', 'ollama')
    ollama_model = payload.get('ollama_model', '')
    min_score = float(payload.get('min_score', MIN_VIRAL_SCORE))
    vertical_format = payload.get('vertical_format', True)
    add_subtitles = payload.get('add_subtitles', True)
    subtitle_style = payload.get('subtitle_style', 'Submagic Yellow')
    banner = payload.get('banner', 'none')
    user_language = payload.get('language', 'en')

    if not streamer_name:
        return jsonify({'error': 'Missing streamer name'}), 400

    # Определяем источник
    if local_file:
        local_path = Path(DOWNLOADS_DIR) / local_file
        if not local_path.exists():
            return jsonify({'error': f'File not found: {local_file}'}), 400
        source_for_cache = local_file
    elif source:
        source_for_cache = source
    else:
        return jsonify({'error': 'Missing source or local file'}), 400

    # Кеш клипов
    cache_key = hashlib.md5(f"{source_for_cache}_{streamer_name}".encode()).hexdigest()
    clips_cache_dir = Path('cache/stream_clips')
    clips_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = clips_cache_dir / f"{cache_key}.json"

    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
            logger.info(f"Loaded stream clips from cache (key: {cache_key[:8]}...)")
            return jsonify({'clips': cached_data})
        except Exception as e:
            logger.warning(f"Failed to load stream cache: {e}")

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            local_path = None
            q.put(("progress", {"step": 1, "total": 5, "message": "Загрузка видео...", "percent": 20}))

            if local_file:
                local_path = str(Path(DOWNLOADS_DIR) / local_file)
                logger.info(f"Using local file: {local_path}")
            elif source and source.startswith('http'):
                try:
                    import yt_dlp
                    downloads_dir = Path(DOWNLOADS_DIR)
                    downloads_dir.mkdir(exist_ok=True)
                    url_hash = hashlib.md5(source.encode()).hexdigest()[:16]
                    existing_files = list(downloads_dir.glob(f"{url_hash}.*"))
                    if existing_files:
                        local_path = str(existing_files[0])
                        logger.info(f"Using existing video file: {local_path}")
                    else:
                        ydl_opts = {
                            'outtmpl': str(downloads_dir / f'{url_hash}.%(ext)s'),
                            'format': 'best[height<=720]',
                            'quiet': True,
                            'no_warnings': True,
                            'socket_timeout': 120,
                            'retries': 20,
                            'geo_bypass': True,
                            'overwrites': False,
                            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                            'extractor_args': {
                                'youtube': {
                                    'skip': ['dash', 'hls'],
                                    'player_client': ['android'],
                                }
                            },
                            'sleep_interval': 1,
                            'max_sleep_interval': 5,
                            'extractor_retries': 10,
                            'file_access_retries': 10,
                            'http_headers': {
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            }
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.extract_info(source, download=True)
                            downloaded_files = list(downloads_dir.glob(f"{url_hash}.*"))
                            if downloaded_files:
                                local_path = str(downloaded_files[0])
                            else:
                                raise Exception("File not found after download")
                except Exception as e:
                    q.put(("error", {"message": f"Ошибка загрузки: {str(e)}"}))
                    return
            else:
                local_path = source
                if not Path(local_path).exists():
                    q.put(("error", {"message": f"Файл не найден: {local_path}"}))
                    return

            if not local_path:
                q.put(("error", {"message": "Не удалось получить видео"}))
                return

            q.put(("progress", {"step": 2, "total": 5, "message": "Транскрипция аудио...", "percent": 40}))
            transcriber = VideoTranscriber()
            transcript = transcriber.transcribe(local_path, language=None)

            # Определяем язык
            if user_language:
                detected_language = user_language
            else:
                detected_language = transcript.get("language", "en")
            logger.info(f"Используемый язык для метаданных: {detected_language}")

            q.put(("progress", {"step": 3, "total": 5, "message": "Поиск вирусных моментов...", "percent": 60}))
            # Используем переданные настройки AI
            analyzer = ViralMomentAnalyzer(provider=ai_provider, model_name=ollama_model)
            moments = analyzer.analyze_transcript(transcript, chunk_duration=30)

            if not moments:
                q.put(("error", {"message": "В стриме не найдено вирусных моментов"}))
                return

            # Фильтруем по порогу
            filtered_moments = [m for m in moments if m['score'] >= min_score]
            if not filtered_moments:
                filtered_moments = moments[:3]

            q.put(("progress", {"step": 4, "total": 5, "message": f"Найдено {len(filtered_moments)} моментов. Нарезка клипов...",
                                "percent": 80}))
            # Создаём обработчик стрима с нужными параметрами
            processor = StreamProcessor(
                local_path,
                streamer_name,
                output_dir='outputs',
                camera_scale=0.3,
                camera_position='top-left'
            )
            clips = processor.process_stream_with_moments(filtered_moments, transcript)

            # Генерируем метаданные для клипов (если нужно)
            # Для стримов можно оставить простые заголовки, но можно и улучшить
            for clip, moment in zip(clips, filtered_moments):
                # Если нужны более креативные названия, раскомментируйте
                # clip['title'] = moment.get('title', clip['title'])
                # clip['description'] = moment.get('description', clip['description'])
                pass

            q.put(("progress", {"step": 5, "total": 5, "message": "Создание клипов завершено", "percent": 100}))

            clips_data = []
            for clip in clips:
                clips_data.append({
                    'path': clip['path'],
                    'filename': Path(clip['path']).name,
                    'start_time': clip['start'],
                    'end_time': clip['end'],
                    'duration': clip['duration'],
                    'title': clip['title'],
                    'description': clip['description']
                })

            try:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(clips_data, f, indent=2, ensure_ascii=False)
                logger.info(f"Saved stream clips to cache (key: {cache_key[:8]}...)")
            except Exception as e:
                logger.warning(f"Failed to save stream cache: {e}")

            q.put(("done", {"clips": clips_data}))

        except Exception as e:
            logger.exception("Ошибка в process_stream worker")
            q.put(("error", {"message": str(e)}))

    def generate():
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while True:
            try:
                event, data = q.get(timeout=30)
            except queue.Empty:
                if t.is_alive():
                    yield ": keepalive\n\n"
                    continue
                yield _sse("error", {"message": "Обработка прервана"})
                break
            yield _sse(event, data)
            if event in ("done", "error"):
                break

    return Response(generate(), mimetype="text/event-stream")


# ===================== ОСНОВНАЯ ОБРАБОТКА (уже с выбором модели) =====================
@app.route("/api/process", methods=["POST"])
def process_video():
    payload = request.get_json(silent=True) or {}
    source = payload.get("source", "youtube")
    url = payload.get("url", "")
    local_file = payload.get("local_file", "")
    quality = payload.get("quality", "best")
    ai_provider = payload.get("provider", "ollama")
    ollama_model = payload.get("ollama_model")
    min_score = float(payload.get("min_score", MIN_VIRAL_SCORE))  # теперь 70 по умолчанию
    vertical_format = payload.get("vertical_format", True)
    add_subtitles = payload.get("add_subtitles", True)
    subtitle_style = payload.get("subtitle_style", "Submagic Yellow")
    banner = payload.get('banner', 'none')
    user_language = payload.get('language')
    banner_path = None
    if banner != 'none':
        banner_path = Path('static/banners') / banner
        if not banner_path.exists():
            banner_path = None

    if source == "youtube" and not url:
        return jsonify({"error": "YouTube URL is required"}), 400
    if source == "local" and not local_file:
        return jsonify({"error": "Local file name is required"}), 400
    if ai_provider == "openai" and not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY not set"}), 400
    if ai_provider == "anthropic" and not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            if source == "youtube":
                q.put(("progress", {"step": 1, "total": 5, "message": "Downloading video...", "percent": 20}))
                downloader = YouTubeDownloader()
                last_pct = [0]

                def _dl_progress(pct, downloaded, total):
                    rounded = int(pct)
                    if rounded >= last_pct[0] + 2 or rounded >= 100:
                        last_pct[0] = rounded
                        q.put(("download_progress", {
                            "percent": min(rounded, 100),
                            "downloaded": downloaded,
                            "total": total,
                        }))

                video_data = downloader.download(url, quality, progress_callback=_dl_progress)
            else:
                q.put(("progress", {"step": 1, "total": 5, "message": "Loading local video...", "percent": 20}))
                save_path = Path(DOWNLOADS_DIR) / local_file
                if not save_path.exists():
                    q.put(("error", {"message": f"File not found: {local_file}"}))
                    return
                processor_probe = VideoProcessor()
                info = processor_probe.get_video_info(str(save_path))
                video_data = {
                    "video_id": None,
                    "title": save_path.stem,
                    "duration": info.get("duration", 0.0),
                    "description": "",
                    "upload_date": "",
                    "uploader": "Local",
                    "view_count": 0,
                    "like_count": 0,
                    "filepath": str(save_path),
                    "url": "Local file",
                }

            q.put(("progress", {"step": 2, "total": 5, "message": "Transcribing audio...", "percent": 40}))
            transcriber = VideoTranscriber()
            transcript = transcriber.transcribe(video_data["filepath"], language=None)

            if user_language:
                detected_language = user_language
            else:
                detected_language = transcript.get("language", "en")
            logger.info(f"Используемый язык для метаданных: {detected_language}")

            q.put(("progress", {"step": 3, "total": 5, "message": "Analyzing for viral moments...", "percent": 60}))
            analyzer = ViralMomentAnalyzer(provider=ai_provider, model_name=ollama_model)
            viral_moments = analyzer.analyze_transcript(transcript, chunk_duration=CHUNK_DURATION)
            if not viral_moments:
                q.put(
                    ("error", {"message": "No viral moments found. Try a different video or lower the minimum score."}))
                return

            high_viral_moments = [m for m in viral_moments if m["score"] >= min_score]
            if not high_viral_moments:
                high_viral_moments = viral_moments[:3]

            q.put(
                ("progress", {"step": 4, "total": 5, "message": "Generating titles & descriptions...", "percent": 80}))
            processor = VideoProcessor()
            refined_moments = analyzer.refine_moments(high_viral_moments, transcript)
            analyzer.generate_clip_metadata(refined_moments, language=detected_language)
            validated_moments = processor.validate_timestamps(video_data["filepath"], refined_moments)

            q.put(("progress",
                   {"step": 5, "total": 5, "message": f"Extracting {len(validated_moments)} clips...", "percent": 100}))

            def extract_single_clip(index, moment):
                output_name = f"clip_{index + 1}_score_{moment['score']:.1f}"
                pre_subtitle_path = processor.extract_clip(
                    video_data["filepath"],
                    moment["start"],
                    moment["end"],
                    output_name,
                    vertical_format=vertical_format,
                    banner_path=banner_path,
                    banner_ratio=0.3
                )
                clip_path = pre_subtitle_path
                if add_subtitles:
                    generator = SubtitleGenerator()
                    output_name = f"clip_{index + 1}_final"
                    clip_path = generator.add_subtitles(
                        pre_subtitle_path,
                        transcript,
                        moment["start"],
                        moment["end"],
                        output_name,
                        vertical_format=vertical_format,
                        clip_start_time=moment["start"],
                        style_template=subtitle_style,
                        language=detected_language,
                    )
                    if os.path.exists(pre_subtitle_path):
                        os.remove(pre_subtitle_path)
                return {
                    "index": index,
                    "path": clip_path,
                    "pre_subtitle_path": pre_subtitle_path,
                    "filename": Path(clip_path).name,
                    "start": moment["start"],
                    "end": moment["end"],
                    "score": moment["score"],
                    "duration": moment["duration"],
                    "reason": moment.get("reason", ""),
                    "title": moment.get("title", ""),
                    "description": moment.get("description", ""),
                }

            clips = []
            max_workers = min(4, len(validated_moments))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(extract_single_clip, i, m): i
                    for i, m in enumerate(validated_moments)
                }
                for future in as_completed(futures):
                    try:
                        clips.append(future.result())
                    except Exception as e:
                        q.put(("error", {"message": f"Failed to extract a clip: {str(e)}"}))

            clips.sort(key=lambda x: x["index"])
            for c in clips:
                c.pop("index", None)

            session_id = str(uuid.uuid4())
            _sessions[session_id] = {
                "transcript": transcript,
                "clips": clips,
                "vertical_format": vertical_format,
                "language": detected_language,
            }

            client_clips = [
                {k: v for k, v in c.items() if k != "pre_subtitle_path"}
                for c in clips
            ]

            q.put(("done", {
                "title": video_data["title"],
                "clips": client_clips,
                "session_id": session_id,
            }))
        except Exception as e:
            logger.exception("Ошибка в process_video worker")
            q.put(("error", {"message": str(e)}))

    def generate():
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while True:
            try:
                event, data = q.get(timeout=30)
            except queue.Empty:
                if t.is_alive():
                    yield ": keepalive\n\n"
                    continue
                yield _sse("error", {"message": "Processing stopped unexpectedly"})
                break
            yield _sse(event, data)
            if event in ("done", "error"):
                break

    return Response(generate(), mimetype="text/event-stream")


# ===================== RESTYLE =====================
@app.route("/api/restyle", methods=["POST"])
def restyle_clip():
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id", "")
    clip_index = payload.get("clip_index")
    style = payload.get("style", "Classic")

    if session_id not in _sessions:
        return jsonify({"error": "Session expired or not found"}), 404

    session_data = _sessions[session_id]
    clips = session_data["clips"]

    if clip_index is None or clip_index < 0 or clip_index >= len(clips):
        return jsonify({"error": "Invalid clip index"}), 400

    clip = clips[clip_index]
    pre_sub_path = clip.get("pre_subtitle_path", "")
    if not pre_sub_path or not Path(pre_sub_path).exists():
        return jsonify({"error": "Pre-subtitle clip not available"}), 404

    try:
        generator = SubtitleGenerator()
        output_name = f"clip_{clip_index + 1}_restyle"
        new_path = generator.add_subtitles(
            pre_sub_path,
            session_data["transcript"],
            clip["start"],
            clip["end"],
            output_name,
            vertical_format=session_data["vertical_format"],
            clip_start_time=clip["start"],
            style_template=style,
            language=session_data["language"],
        )
        new_filename = Path(new_path).name
        clip["path"] = new_path
        clip["filename"] = new_filename
        return jsonify({"filename": new_filename})
    except Exception as e:
        logger.exception("Ошибка в restyle_clip")
        return jsonify({"error": str(e)}), 500


# ===================== ЗАПУСК =====================
if __name__ == "__main__":
    if not check_dependencies(provider='ollama'):
        logger.warning("Некоторые зависимости не найдены. Проверьте установку.")
    if app.debug:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        logger.info("OAUTHLIB_INSECURE_TRANSPORT включён (режим разработки)")
    app.run(debug=True, use_reloader=False, port=5000)