from celery import shared_task
from app.database import SessionLocal
from app.models import ProcessingJob
from app.modules import downloader, transcriber, analyzer, video_processor, subtitle_generator
from app.utils.websocket_manager import manager
import os
import json

@shared_task(bind=True)
def process_video_job(self, job_id: str):
    db = SessionLocal()
    job = db.query(ProcessingJob).filter_by(id=job_id).first()
    if not job:
        return {"error": "Job not found"}

    def update_progress(progress, message=""):
        job.progress = progress
        db.commit()
        # Отправка WebSocket (можно использовать async, но в celery это сложно)
        # Лучше через менеджер, но нужен event loop
        # Для простоты оставим обновление в БД

    try:
        update_progress(0, "Начинаем обработку")
        # 1. Получение видео
        if job.source_type == "youtube":
            video_path = downloader.download(job.source_url)
        else:
            # Генерация из текста (заглушка)
            video_path = "temp_video.mp4"
        update_progress(20, "Видео загружено")

        # 2. Транскрипция
        transcript = transcriber.transcribe(video_path)
        update_progress(40, "Транскрипция выполнена")

        # 3. Анализ
        moments = analyzer.analyze_transcript(transcript)
        update_progress(60, f"Найдено {len(moments)} моментов")

        # 4. Нарезка клипов
        clips = []
        num_clips = job.options.get("num_clips", 5) if job.options else 5
        for i, moment in enumerate(moments[:num_clips]):
            clip_path = video_processor.extract_clip(
                video_path,
                moment['start'],
                moment['end'],
                vertical=True
            )
            final_path = subtitle_generator.add_subtitles(
                clip_path,
                transcript,
                moment['start'],
                moment['end'],
                style_template=job.options.get("subtitle_style", "Classic") if job.options else "Classic"
            )
            clips.append({
                "path": final_path,
                "score": moment.get("score", 0),
                "duration": moment["end"] - moment["start"],
                "start": moment["start"],
                "end": moment["end"]
            })
            update_progress(60 + (i+1) * (40/num_clips), f"Клип {i+1} готов")

        job.status = "completed"
        job.result_data = {"clips": clips}
        job.progress = 100
        db.commit()
        return {"status": "completed"}

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        db.commit()
        raise e
    finally:
        db.close()