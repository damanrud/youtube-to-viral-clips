import os
import sys
import time as _time
from pathlib import Path
from typing import Optional, Dict, List
import json
from datetime import timedelta
import subprocess
import shutil  # <-- добавлен для поиска в PATH


def format_time(seconds: float) -> str:
    td = timedelta(seconds=seconds)
    hours = int(td.total_seconds() // 3600)
    minutes = int((td.total_seconds() % 3600) // 60)
    secs = int(td.total_seconds() % 60)

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


def get_ffmpeg_path() -> str:
    """
    Возвращает путь к исполняемому файлу FFmpeg.
    Приоритет:
      1. Переменная окружения FFMPEG_PATH (если задана и файл существует)
      2. Поиск в системном PATH (через shutil.which)
    Если FFmpeg не найден, выбрасывает RuntimeError.
    """
    custom_path = os.getenv("FFMPEG_PATH")
    if custom_path and os.path.exists(custom_path):
        return custom_path

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    raise RuntimeError(
        "FFmpeg не найден. Установите FFmpeg или задайте переменную окружения FFMPEG_PATH."
    )


def set_ffmpeg_env():
    """
    Устанавливает переменную окружения IMAGEIO_FFMPEG_EXE,
    чтобы библиотеки (imageio, moviepy) использовали нужный FFmpeg.
    """
    try:
        path = get_ffmpeg_path()
        os.environ["IMAGEIO_FFMPEG_EXE"] = path
        return path
    except RuntimeError as e:
        # Можно залогировать, но здесь просто пробрасываем дальше
        raise


def check_dependencies(provider: str = "ollama") -> bool:
    """
    Проверяет наличие необходимых зависимостей:
      - FFmpeg (обязательно)
      - Ollama (если выбран провайдер)
    Возвращает True, если всё найдено.
    """
    missing = []

    # Проверяем FFmpeg
    try:
        get_ffmpeg_path()
    except RuntimeError:
        missing.append((
            'ffmpeg',
            'FFmpeg is required for video processing. Install from: https://ffmpeg.org/download.html'
        ))

    # Проверяем Ollama, если нужно
    if provider == "ollama":
        if not is_command_available('ollama'):
            missing.append((
                'ollama',
                'Ollama is required for LLM analysis. Install from: https://ollama.ai/'
            ))

    if missing:
        # Выводим сообщение, но не прерываем выполнение
        for cmd, msg in missing:
            print(f"⚠️  Missing dependency: {cmd} – {msg}")
        return False

    return True


def is_command_available(command: str) -> bool:
    """Проверяет, доступна ли команда в системе (через PATH)."""
    try:
        subprocess.run([command, '--version'],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def load_json_file(filepath: Path) -> Optional[Dict]:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_json_file(data: Dict, filepath: Path) -> bool:
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def clean_filename(filename: str) -> str:
    import re
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.strip('. ')
    return filename[:200]


def get_file_size_mb(filepath: Path) -> float:
    if filepath.exists():
        return filepath.stat().st_size / (1024 * 1024)
    return 0.0


def print_moments_table(moments: List[Dict]):
    # Display moments in table format (можно реализовать позже)
    pass


def select_moments(moments: List[Dict], max_clips: Optional[int] = None) -> List[Dict]:
    if not moments:
        return []
    if max_clips and len(moments) > max_clips:
        moments = moments[:max_clips]
    return moments


def estimate_processing_time(num_clips: int, total_duration: float) -> str:
    time_per_clip = 30
    transcription_time = total_duration * 0.3
    analysis_time = num_clips * 5
    total_seconds = transcription_time + analysis_time + (num_clips * time_per_clip)
    return format_time(total_seconds)


def create_summary_report(video_metadata: Dict, moments: List[Dict],
                          output_clips: List[str]) -> str:
    report_path = Path(output_clips[0]).parent / "processing_summary.txt" if output_clips else "processing_summary.txt"

    with open(report_path, 'w') as f:
        f.write("YouTube Video Viral Clips - Processing Summary\n")
        f.write("=" * 50 + "\n\n")

        f.write("Original Video:\n")
        f.write(f"  Title: {video_metadata.get('title', 'Unknown')}\n")
        f.write(f"  Duration: {format_time(video_metadata.get('duration', 0))}\n")
        f.write(f"  URL: {video_metadata.get('url', 'N/A')}\n\n")

        f.write(f"Viral Moments Found: {len(moments)}\n")
        f.write(f"Clips Generated: {len(output_clips)}\n\n")

        f.write("Generated Clips:\n")
        for i, (clip_path, moment) in enumerate(zip(output_clips, moments)):
            f.write(f"\n{i + 1}. {Path(clip_path).name}\n")
            f.write(f"   Time: {format_time(moment['start'])} - {format_time(moment['end'])}\n")
            f.write(f"   Score: {moment['score']}/10\n")
            f.write(f"   Reason: {moment['reason']}\n")

    return str(report_path)


def cleanup_downloads(downloads_dir: Path, max_age_hours: int = 24) -> int:
    """Remove downloaded video files older than max_age_hours. Returns count of deleted files."""
    if not downloads_dir.exists():
        return 0
    cutoff = _time.time() - (max_age_hours * 3600)
    deleted = 0
    for path in downloads_dir.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            deleted += 1
    return deleted


class ProgressBar:
    def __init__(self, total: int, description: str = "Progress"):
        self.total = total
        self.current = 0
        self.description = description

    def update(self, increment: int = 1):
        self.current += increment
        progress = self.current / self.total
        bar_length = 40
        filled_length = int(bar_length * progress)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        percent = progress * 100
        # Можно выводить в консоль, но здесь оставлено как заглушка
        if self.current >= self.total:
            pass  # или print(f"\r{self.description}: {bar} {percent:.1f}%")

    def finish(self):
        self.current = self.total
        self.update(0)