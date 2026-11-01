"""Error helpers shared by the endpoints: Spanish messages for users, raw
errors only in the server log.
"""
import logging

from fastapi import HTTPException


logger = logging.getLogger("uvicorn.error")


def processing_error(message: str, error: Exception) -> HTTPException:
    # The raw error can expose internals (paths, library messages), so users
    # only get the Spanish message and the details go to the server log.
    logger.warning(message, exc_info=error)
    return HTTPException(status_code=400, detail=message)


ENCRYPTED_PDF_MESSAGE = (
    "Este PDF está protegido con contraseña. Desbloquéalo antes de usar esta herramienta."
)


def encrypted_pdf() -> HTTPException:
    return HTTPException(status_code=400, detail=ENCRYPTED_PDF_MESSAGE)
