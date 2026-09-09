"""Bounds for database IDs, shared by routes that look up public resources."""
from typing import Annotated

from fastapi import Path

MAX_RESOURCE_ID = 2_147_483_647
ResourceId = Annotated[int, Path(ge=1, le=MAX_RESOURCE_ID)]
