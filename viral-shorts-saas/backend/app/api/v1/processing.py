from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid
from app.database import get_db
from app.models import User, ProcessingJob
from app.schemas import SubmitRequest, JobResponse
from app.dependencies import get_current_user
from app.credits import check_credits, deduct_credits
from app.tasks.video_processing import process_video_job

router = APIRouter(prefix="/processing", tags=["processing"])


@router.post("/submit", response_model=JobResponse)
def submit_job(req: SubmitRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cost = 5  # стоимость обработки в кредитах
    if not check_credits(db, user.id, cost):
        raise HTTPException(400, "Not enough credits")

    job = ProcessingJob(
        id=uuid.uuid4(),
        user_id=user.id,
        source_type=req.source_type,
        source_url=req.source_url,
        text_input=req.text_input,
        options=req.options,
        status="pending"
    )
    db.add(job)
    deduct_credits(db, user.id, cost, "processing", f"Обработка задачи {job.id}")
    db.commit()
    db.refresh(job)

    # Запускаем Celery задачу
    process_video_job.delay(str(job.id))

    return JobResponse(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        created_at=job.created_at
    )


@router.get("/status/{job_id}", response_model=JobResponse)
def get_status(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id, ProcessingJob.user_id == user.id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    return JobResponse(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        result_data=job.result_data if job.status == "completed" else None,
        error=job.error,
        created_at=job.created_at
    )


@router.get("/result/{job_id}")
def get_result(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id, ProcessingJob.user_id == user.id).first()
    if not job or job.status != "completed":
        raise HTTPException(404, "Result not ready")
    return job.result_data