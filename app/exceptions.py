"""Domain errors. Each carries the HTTP status it should be reported with."""

from __future__ import annotations


class ApiError(Exception):
    status_code = 400

    def __init__(self, message: str, file_id: str | None = None):
        super().__init__(message)
        self.message = message
        self.file_id = file_id


class UnsupportedFileTypeError(ApiError):
    status_code = 415


class FileTooLargeError(ApiError):
    status_code = 413


class FileProcessingError(ApiError):
    """The upload was received but its contents could not be processed."""

    status_code = 422


class ResourceNotFoundError(ApiError):
    status_code = 404


class ConflictError(ApiError):
    status_code = 409
