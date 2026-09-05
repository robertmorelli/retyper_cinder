"""General helpers for consuming iterables."""


def first(items):
    """Return the first truthy item."""
    return next(item for item in items if item)
