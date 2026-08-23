"""Remove Static Python annotations while preserving compilability."""

from .detyper import detype

__all__ = ["detype"]
