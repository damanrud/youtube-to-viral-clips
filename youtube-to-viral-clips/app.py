import json
import queue
import threading
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import secrets  # <-- добавлено для PKCE
import base64  # <-- добавлено для PKCE

from flask import Flask, render_template, request, jsonify, Response, send_from_directory, session, redirect, url_for
import os

os.environ["IMAGEIO_FFMPEG_EXE"] = r"C:\ffmpeg\bin\ffmpeg.exe"
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # <-- добавлено для локального OAuth

# Импорты модулей
from modules.downloader import YouTubeDownloader
from modules.transcriber import VideoTranscriber
from modules.analyzer import ViralMomentAnalyzer
from modules.video_processor import VideoProcessor
from modules.subtitle_generator import SubtitleGenerator
from modules.social_publisher import save_metadata, upload_to_youtube, upload_to_tiktok
from modules.stream_processor import StreamProcessor
from modules.youtube_auth import (  # НОВОЕ: импорт функций для OAuth
    get_oauth_flow, get_credentials_from_session,
    save_credentials_to_session, refresh_credentials_if_needed
)
from utils.helpers import check_dependencies, clean_filename
from config import (
    SUBTITLE_TEMPLATES, OPENAI_API_KEY, ANTHROPIC_API_KEY,
    DOWNLOADS_DIR, OUTPUTS_DIR, SUPPORTED_FORMATS, CHUNK_DURATION,
)
# НОВОЕ: импорт для Google API
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)
app.secret_key = os.urandom(24)  # НОВОЕ: ключ для сессий

# In-memory session storage for restyle feature and OAuth code_verifier
_sessions: dict = {}


# ---------------------------------------------------------------------------
# PKCE helper
# ---------------------------------------------------------------------------
def generate_code_verifier():
    """Генерирует безопасный code_verifier для PKCE."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
    # Убираем символы, которые могут вызвать проблемы
    code_verifier = code_verifier.replace('=', '').replace('+', '-').replace('/', '_')
    return code_verifier

def generate_code_challenge(code_verifier):
    """Вычисляет code_challenge (SHA256 + base64url) для PKCE."""
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
    """Format a Server-Sent Event."""
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


# ===================== МАРШРУТЫ ДЛЯ YOUTUBE OAuth (используем state) =====================
@app.route("/api/youtube/auth")
def youtube_auth():
    """Начинает OAuth-поток для YouTube с PKCE."""
    flow = get_oauth_flow()
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)  # <-- вычисляем хеш
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        code_challenge_method='S256',
        code_challenge=code_challenge,  # <-- передаём хеш
    )
    # Сохраняем code_verifier по ключу state (state придёт в callback)
    _sessions[state] = {'code_verifier': code_verifier}
    print(f"[AUTH] Сохранили code_verifier для state {state[:8]}")
    return redirect(authorization_url)


@app.route("/api/youtube/callback")
def youtube_callback():
    """Обрабатывает обратный вызов от Google OAuth с PKCE."""
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

    print(f"[CALLBACK] Используем code_verifier для state {state[:8]}")

    flow.fetch_token(
        authorization_response=request.url,
        code_verifier=code_verifier
    )
    credentials = flow.credentials
    save_credentials_to_session(credentials)
    return redirect(url_for('index'))


@app.route("/api/youtube/status", methods=["GET"])
def youtube_status():
    """Возвращает статус авторизации в YouTube."""
    creds = get_credentials_from_session()
    if creds and not creds.expired:
        return jsonify({'authenticated': True})
    return jsonify({'authenticated': False})


@app.route("/api/youtube/logout", methods=["POST"])
def youtube_logout():
    """Выход из YouTube (очистка сессии)."""
    session.pop('youtube_token', None)
    return jsonify({'status': 'ok'})


# ===================== ОБНОВЛЁННЫЙ ЭНДПОИНТ ПУБЛИКАЦИИ =====================
@app.route("/api/publish", methods=["POST"])
def publish_video():
    """Публикует видео на YouTube, используя токен из сессии."""
    data = request.get_json(silent=True) or {}
    video_path = data.get('video_path')
    title = data.get('title', '')
    description = data.get('description', '')
    tags = data.get('tags', [])
    privacy = data.get('privacy', 'private')
    save_only = data.get('save_only', False)

    if not video_path or not Path(video_path).exists():
        return jsonify({'error': 'Video file not found'}), 400

    # Сохраняем метаданные в .txt (всегда)
    txt_path = save_metadata(video_path, {
        'title': title,
        'description': description,
        'tags': tags,
        'platform': 'youtube'
    })

    # Если только сохранение TXT
    if save_only:
        return jsonify({'status': 'ok', 'txt_path': txt_path})

    # Получаем учётные данные из сессии
    creds = get_credentials_from_session()
    if not creds:
        return jsonify({'error': 'Not authenticated with YouTube. Please log in.'}), 401
    if creds.expired:
        if not refresh_credentials_if_needed(creds):
            return jsonify({'error': 'Credentials expired. Please re-authenticate.'}), 401

    try:
        youtube = build('youtube', 'v3', credentials=creds)
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22'  # People & Blogs
            },
            'status': {
                'privacyStatus': privacy  # 'private', 'unlisted', 'public'
            }
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )
        response = request.execute()
        return jsonify({'status': 'ok', 'result': response, 'txt_path': txt_path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===================== СТРИМ-РЕЖИМ С SSE И КЕШИРОВАНИЕМ =====================
@app.route("/api/process_stream", methods=["POST"])
def process_stream():
    """
    Обрабатывает стрим с отправкой прогресса через SSE.
    Поддерживает кеширование скачанных видео и результатов клипов.
    """
    payload = request.get_json(silent=True) or {}
    source = payload.get('source')
    streamer_name = payload.get('streamer_name')

    if not source or not streamer_name:
        return jsonify({'error': 'Missing source or streamer name'}), 400

    # Генерируем ключ для кеширования клипов на основе источника и имени стримера
    cache_key = hashlib.md5(f"{source}_{streamer_name}".encode()).hexdigest()
    clips_cache_dir = Path('cache/stream_clips')
    clips_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = clips_cache_dir / f"{cache_key}.json"

    # Если есть кешированные клипы, возвращаем их
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
            print(f"✓ Loaded stream clips from cache (key: {cache_key[:8]}...)")
            # Проверяем, что все файлы существуют (можно опционально)
            return jsonify({'clips': cached_data})
        except Exception as e:
            print(f"Warning: Failed to load stream cache: {e}")

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            local_path = None
            # Шаг 1: Загрузка видео (с проверкой существования)
            q.put(("progress", {"step": 1, "total": 5, "message": "Загрузка стрима...", "percent": 20}))

            if source.startswith('http'):
                try:
                    import yt_dlp
                    downloads_dir = Path('downloads')
                    downloads_dir.mkdir(exist_ok=True)
                    url_hash = hashlib.md5(source.encode()).hexdigest()[:16]
                    # Ищем уже скачанный файл
                    existing_files = list(downloads_dir.glob(f"{url_hash}.*"))
                    if existing_files:
                        local_path = str(existing_files[0])
                        print(f"✓ Using existing video file: {local_path}")
                    else:
                        ydl_opts = {
                            'outtmpl': str(downloads_dir / f'{url_hash}.%(ext)s'),
                            'format': 'best[height<=720]',
                            'quiet': True,
                            'no_warnings': True,
                            'socket_timeout': 120,
                            'retries': 10,
                            'geo_bypass': True,
                            'overwrites': False,
                            'extractor_args': {
                                'twitch': {
                                    'disable_polling': ['true'],
                                }
                            }
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(source, download=True)
                            # Находим скачанный файл
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

            q.put(("progress", {"step": 2, "total": 5, "message": "Транскрипция аудио...", "percent": 40}))

            # Шаг 2: Транскрипция
            transcriber = VideoTranscriber()
            transcript = transcriber.transcribe(local_path, language=None)

            q.put(("progress", {"step": 3, "total": 5, "message": "Поиск вирусных моментов...", "percent": 60}))

            # Шаг 3: Анализ
            analyzer = ViralMomentAnalyzer(provider='ollama')
            moments = analyzer.analyze_transcript(transcript, chunk_duration=30)

            if not moments:
                q.put(("error", {"message": "В стриме не найдено вирусных моментов"}))
                return

            q.put(("progress", {"step": 4, "total": 5, "message": f"Найдено {len(moments)} моментов. Нарезка клипов...",
                                "percent": 80}))

            # Шаг 4: Нарезка с наложением веб-камеры
            from modules.stream_processor import StreamProcessor
            processor = StreamProcessor(local_path, streamer_name, output_dir='outputs')
            clips = processor.process_stream_with_moments(moments, transcript)

            q.put(("progress", {"step": 5, "total": 5, "message": "Создание клипов завершено", "percent": 100}))

            # Формируем результат
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

            # Сохраняем в кеш
            try:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(clips_data, f, indent=2, ensure_ascii=False)
                print(f"✓ Saved stream clips to cache (key: {cache_key[:8]}...)")
            except Exception as e:
                print(f"Warning: Failed to save stream cache: {e}")

            q.put(("done", {"clips": clips_data}))

        except Exception as e:
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


# ===================== ОСНОВНАЯ ОБРАБОТКА ВИДЕО =====================

@app.route("/api/process", methods=["POST"])
def process_video():
    """Run the full pipeline and stream progress via SSE."""
    payload = request.get_json(silent=True) or {}

    source = payload.get("source", "youtube")
    url = payload.get("url", "")
    local_file = payload.get("local_file", "")
    quality = payload.get("quality", "best")
    ai_provider = payload.get("provider", "ollama")
    min_score = float(payload.get("min_score", 7.0))
    vertical_format = payload.get("vertical_format", True)
    add_subtitles = payload.get("add_subtitles", True)
    subtitle_style = payload.get("subtitle_style", "Submagic Yellow")

    # --- Получение баннера ---
    banner = payload.get('banner', 'none')
    banner_path = None
    if banner != 'none':
        banner_path = Path('static/banners') / banner
        if not banner_path.exists():
            banner_path = None

    # Validate inputs
    if source == "youtube" and not url:
        return jsonify({"error": "YouTube URL is required"}), 400
    if source == "local" and not local_file:
        return jsonify({"error": "Local file name is required"}), 400

    # Check API keys
    if ai_provider == "openai" and not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY not set"}), 400
    if ai_provider == "anthropic" and not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400

    # We use a queue so the background worker can push SSE messages
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            # Step 1 — Acquire video
            if source == "youtube":
                q.put(("progress",
                       {"step": 1, "total": 5, "message": "Downloading video from YouTube...", "percent": 20}))
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

            # Step 2 — Transcribe
            q.put(("progress", {"step": 2, "total": 5, "message": "Transcribing audio...", "percent": 40}))
            transcriber = VideoTranscriber()
            transcript = transcriber.transcribe(video_data["filepath"], language=None)
            detected_language = transcript.get("language", "en")

            # Step 3 — Analyze
            q.put(("progress", {"step": 3, "total": 5, "message": "Analyzing for viral moments...", "percent": 60}))
            analyzer = ViralMomentAnalyzer(provider=ai_provider)
            viral_moments = analyzer.analyze_transcript(transcript, chunk_duration=CHUNK_DURATION)

            if not viral_moments:
                q.put(
                    ("error", {"message": "No viral moments found. Try a different video or lower the minimum score."}))
                return

            high_viral_moments = [m for m in viral_moments if m["score"] >= min_score]
            if not high_viral_moments:
                high_viral_moments = viral_moments[:3]

            # Step 4 — Generate metadata (titles & descriptions)
            q.put(
                ("progress", {"step": 4, "total": 5, "message": "Generating titles & descriptions...", "percent": 80}))
            processor = VideoProcessor()
            refined_moments = analyzer.refine_moments(high_viral_moments, transcript)
            analyzer.generate_clip_metadata(refined_moments, language=detected_language)
            validated_moments = processor.validate_timestamps(video_data["filepath"], refined_moments)

            # Step 5 — Extract clips
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

            # Store session data for restyle feature
            session_id = str(uuid.uuid4())
            _sessions[session_id] = {
                "transcript": transcript,
                "clips": clips,
                "vertical_format": vertical_format,
                "language": detected_language,
            }

            client_clips = [
                {k: v for k, v in c.items() if k not in ("path", "pre_subtitle_path")}
                for c in clips
            ]

            q.put(("done", {
                "title": video_data["title"],
                "clips": client_clips,
                "session_id": session_id,
            }))

        except Exception as e:
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


@app.route("/api/restyle", methods=["POST"])
def restyle_clip():
    """Re-apply subtitles with a different style on an existing clip."""
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id", "")
    clip_index = payload.get("clip_index")
    style = payload.get("style", "Classic")

    if session_id not in _sessions:
        return jsonify({"error": "Session expired or not found"}), 404

    session = _sessions[session_id]
    clips = session["clips"]

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
            session["transcript"],
            clip["start"],
            clip["end"],
            output_name,
            vertical_format=session["vertical_format"],
            clip_start_time=clip["start"],
            style_template=style,
            language=session["language"],
        )
        new_filename = Path(new_path).name
        clip["path"] = new_path
        clip["filename"] = new_filename
        return jsonify({"filename": new_filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5000)