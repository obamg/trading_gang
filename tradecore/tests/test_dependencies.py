"""Dependency tests — plan-based authorization guard."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import require_feature, user_has_access
from app.errors import AppError


# ---------- user_has_access ----------

@pytest.mark.asyncio
async def test_user_has_access_granted():
    """User with a plan that includes the feature should get True."""
    mock_plan = MagicMock()
    mock_plan.features = {"radarx": True, "oracle": True}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_plan

    db = AsyncMock()
    db.execute.return_value = mock_result

    assert await user_has_access(db, "user-123", "radarx") is True


@pytest.mark.asyncio
async def test_user_has_access_denied():
    """User with a plan that excludes the feature should get False."""
    mock_plan = MagicMock()
    mock_plan.features = {"radarx": True, "oracle": False}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_plan

    db = AsyncMock()
    db.execute.return_value = mock_result

    assert await user_has_access(db, "user-123", "oracle") is False


@pytest.mark.asyncio
async def test_user_has_access_no_plan():
    """User with no active subscription should get False."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute.return_value = mock_result

    assert await user_has_access(db, "user-123", "radarx") is False


@pytest.mark.asyncio
async def test_user_has_access_missing_feature_key():
    """Feature not in plan.features dict should default to False."""
    mock_plan = MagicMock()
    mock_plan.features = {"radarx": True}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_plan

    db = AsyncMock()
    db.execute.return_value = mock_result

    assert await user_has_access(db, "user-123", "nonexistent_feature") is False


# ---------- require_feature ----------

@pytest.mark.asyncio
async def test_require_feature_blocks_free_user():
    """A free user trying to access a pro feature should get 403."""
    guard = require_feature("oracle")

    mock_plan = MagicMock()
    mock_plan.features = {"oracle": False}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_plan

    db = AsyncMock()
    db.execute.return_value = mock_result

    user = MagicMock()
    user.id = "free-user-id"

    with pytest.raises(AppError) as exc_info:
        await guard(user=user, db=db)
    assert exc_info.value.status_code == 403
    assert "FEATURE_NOT_AVAILABLE" in str(exc_info.value.code)
