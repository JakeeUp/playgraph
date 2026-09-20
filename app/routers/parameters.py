"""Bounds for database IDs and pagination, shared by routes that look up public resources."""
from typing import Annotated

from fastapi import Path

MAX_RESOURCE_ID = 2_147_483_647
ResourceId = Annotated[int, Path(ge=1, le=MAX_RESOURCE_ID)]

# A deep offset makes the database scan and discard every skipped row, so the
# public paginated routes stop well before that turns into free work for an
# anonymous caller. The feed already refused to page past this; the catalog,
# review and comment listings now refuse at the same point. Anything that
# genuinely needs to go further pages with a `before` anchor instead.
MAX_OFFSET = 10_000
