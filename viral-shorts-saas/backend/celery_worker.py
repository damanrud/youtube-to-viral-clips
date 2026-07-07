from app.tasks.celery_app import app
from app.tasks import video_processing  # noqa: F401

if __name__ == "__main__":
    app.start()