from __future__ import annotations

from typing import Any, Optional


class MusicServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class BadRequestError(MusicServiceError):
    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(
            message,
            code="bad_request",
            status_code=400,
            details=details,
        )


class ProviderNotFoundError(MusicServiceError):
    def __init__(self, source: str) -> None:
        super().__init__(
            f"Unknown source '{source}'.",
            code="provider_not_found",
            status_code=404,
            details={"source": source},
        )


class ProviderUnavailableError(MusicServiceError):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(
            message,
            code="provider_unavailable",
            status_code=503,
            details={"source": source},
        )


class TrackPreparationError(MusicServiceError):
    def __init__(
        self,
        source: str,
        track_id: str,
        message: str,
        *,
        cause: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code="track_preparation_failed",
            status_code=502,
            details={
                "source": source,
                "track_id": track_id,
                **({"cause": cause} if cause else {}),
            },
        )


class CacheEntryNotFoundError(MusicServiceError):
    def __init__(self, cache_key: str) -> None:
        super().__init__(
            "The requested cached track was not found.",
            code="cache_entry_not_found",
            status_code=404,
            details={"cache_key": cache_key},
        )


class SearchResultNotFoundError(MusicServiceError):
    def __init__(self, result_id: str) -> None:
        super().__init__(
            "The selected search result is no longer available. Please run the search again.",
            code="search_result_not_found",
            status_code=404,
            details={"result_id": result_id},
        )
