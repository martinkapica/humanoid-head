from dataclasses import dataclass


@dataclass(slots=True)
class DomainError(Exception):
    code: str
    message: str
    http_status: int = 400

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ValidationError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("VALIDATION_FAILED", message, 400)
