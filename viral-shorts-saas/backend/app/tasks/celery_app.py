from celery import Celery
from app.config import settings

app = Celery(
    "viral_shorts",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.video_processing"]
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)