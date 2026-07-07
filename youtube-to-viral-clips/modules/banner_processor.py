import ffmpeg
from pathlib import Path

def apply_banner(input_path, output_path, banner_path, height_ratio=0.3):
    """
    Накладывает баннер (изображение, GIF или видео) поверх видео.
    Баннер занимает height_ratio от высоты видео (по умолчанию 30%).
    Сохраняет оригинальный звук.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    banner_path = Path(banner_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input video not found: {input_path}")
    if not banner_path.exists():
        raise FileNotFoundError(f"Banner file not found: {banner_path}")

    # Получаем размеры видео
    probe = ffmpeg.probe(str(input_path))
    video_stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    width = int(video_stream['width'])
    height = int(video_stream['height'])
    banner_h = int(height * height_ratio)

    # Создаём потоки
    video = ffmpeg.input(str(input_path))
    banner = ffmpeg.input(str(banner_path))

    # Масштабируем баннер до ширины видео и вычисленной высоты
    scaled_banner = ffmpeg.filter(banner, 'scale', width, banner_h)

    # Накладываем баннер на видео (сверху, x=0, y=0)
    overlaid = ffmpeg.overlay(video, scaled_banner, x=0, y=0)

    # Сохраняем результат с аудио из исходного видео
    output = ffmpeg.output(
        overlaid,
        video.audio,          # <-- добавляем аудио
        str(output_path),
        acodec='copy',
        vcodec='libx264',
        preset='veryfast',
        crf=23
    )

    ffmpeg.run(output, overwrite_output=True, quiet=False)
    return str(output_path)