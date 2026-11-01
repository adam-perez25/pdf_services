"""Cancel a running job (the page's "Cancelar" button)."""
from fastapi import APIRouter, HTTPException

from app.cancellation import JOB_ID_PATTERN, cancel_job


router = APIRouter()


@router.post("/api/jobs/{job_id}/cancel")
def cancel_running_job(job_id: str):
    # Unknown or already finished ids are not an error: the job just ended first.
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
    return {"cancelled": cancel_job(job_id)}
