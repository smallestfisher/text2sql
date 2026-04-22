from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PermissionDeniedError(AppError):
    def __init__(self, message: str = "permission denied") -> None:
        super().__init__(message=message, status_code=403)


class ResourceNotFoundError(AppError):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message=message, status_code=404)
