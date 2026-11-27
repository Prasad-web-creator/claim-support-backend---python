"""Serialize PyMuPDF access across the backend's document workers."""

from functools import wraps
from threading import RLock


pdf_access = RLock()


def serialized_pdf(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with pdf_access:
            return function(*args, **kwargs)

    return wrapped
