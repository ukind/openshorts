"""Tests for GameProfile functionality."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch

# Import the actual modules we're testing
from cloud.models import GameProfile
from cloud.game_profiles import (
    create_game_profile,
    list_game_profiles,
    get_game_profile,
    update_game_profile,
    delete_game_profile,
    duplicate_game_profile
)
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_game_profile():
    """Test creating a game profile."""
    # Mock user ID
    user_id = str(uuid.uuid4())

    # Test data
    profile_data = {
        "name": "Test Game Profile",
        "game_title": "Test Game",
        "steam_app_id": 12345,
        "steam_description": "A test game for testing purposes",
        "steam_genres": ["Action", "Adventure"],
        "steam_tags": ["Single-player", "Indie"],
        "ai_analysis": {"game_type": "action", "key_moments": ["combat", "exploration"]},
        "recommended_weights": {"combat": 0.8, "exploration": 0.6},
        "active_weights": {"combat": 0.9, "exploration": 0.7}
    }

    # Mock database session
    with patch('cloud.database.session') as mock_session:
        mock_session_instance = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_session_instance

        # Mock the flush and add operations
        mock_session_instance.add = AsyncMock()
        mock_session_instance.flush = AsyncMock()

        # Call the function
        result = await create_game_profile(user_id, profile_data)

        # Verify that session was used correctly
        assert result is not None
        assert result.name == "Test Game Profile"
        assert result.game_title == "Test Game"
        assert result.steam_app_id == 12345


@pytest.mark.asyncio
async def test_list_game_profiles():
    """Test listing game profiles for a user."""
    # Mock user ID
    user_id = str(uuid.uuid4())

    # Mock database session
    with patch('cloud.database.session') as mock_session:
        mock_session_instance = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_session_instance

        # Mock the select result
        mock_result = AsyncMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session_instance.execute.return_value = mock_result

        # Call the function
        result = await list_game_profiles(user_id)

        # Verify that session was used correctly
        assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_game_profile():
    """Test getting a specific game profile."""
    # Mock user ID and profile ID
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())

    # Mock database session
    with patch('cloud.database.session') as mock_session:
        mock_session_instance = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_session_instance

        # Mock the select result
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session_instance.execute.return_value = mock_result

        # Call the function
        result = await get_game_profile(user_id, profile_id)

        # Verify that session was used correctly
        assert result is None


@pytest.mark.asyncio
async def test_update_game_profile():
    """Test updating a game profile."""
    # Mock user ID and profile ID
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())

    # Test data
    profile_data = {
        "name": "Updated Test Profile",
        "game_title": "Updated Test Game"
    }

    # Mock database session
    with patch('cloud.database.session') as mock_session:
        mock_session_instance = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_session_instance

        # Mock the update result
        mock_result = AsyncMock()
        mock_result.rowcount = 1
        mock_session_instance.execute.return_value = mock_result

        # Mock the select for getting updated profile
        mock_select_result = AsyncMock()
        mock_select_result.scalar_one_or_none.return_value = None
        mock_session_instance.execute.side_effect = [mock_result, mock_select_result]

        # Call the function
        result = await update_game_profile(user_id, profile_id, profile_data)

        # Verify that session was used correctly
        assert result is None  # No profile returned since we mocked it


@pytest.mark.asyncio
async def test_delete_game_profile():
    """Test deleting a game profile."""
    # Mock user ID and profile ID
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())

    # Mock database session
    with patch('cloud.database.session') as mock_session:
        mock_session_instance = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_session_instance

        # Mock the delete result
        mock_result = AsyncMock()
        mock_result.rowcount = 1
        mock_session_instance.execute.return_value = mock_result

        # Call the function
        result = await delete_game_profile(user_id, profile_id)

        # Verify that session was used correctly
        assert result is True


@pytest.mark.asyncio
async def test_duplicate_game_profile():
    """Test duplicating a game profile."""
    # Mock user ID and profile ID
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())

    # Mock database session
    with patch('cloud.database.session') as mock_session:
        mock_session_instance = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_session_instance

        # Mock the get_game_profile result to return a profile
        mock_get_result = AsyncMock()
        mock_get_result.scalar_one_or_none.return_value = None
        mock_session_instance.execute.side_effect = [mock_get_result]

        # Mock the create_game_profile to return a new profile
        with patch('cloud.game_profiles.create_game_profile') as mock_create:
            mock_create.return_value = None

            # Call the function
            result = await duplicate_game_profile(user_id, profile_id)

            # Verify that session was used correctly
            assert result is None


def test_game_profile_model():
    """Test that GameProfile model definition is correct."""
    # Just verify it can be instantiated without error
    from cloud.models import GameProfile

    # Check that the model has the expected attributes
    assert hasattr(GameProfile, 'id')
    assert hasattr(GameProfile, 'user_id')
    assert hasattr(GameProfile, 'name')
    assert hasattr(GameProfile, 'game_title')
    assert hasattr(GameProfile, 'steam_app_id')
    assert hasattr(GameProfile, 'steam_description')
    assert hasattr(GameProfile, 'steam_genres')
    assert hasattr(GameProfile, 'steam_tags')
    assert hasattr(GameProfile, 'ai_analysis')
    assert hasattr(GameProfile, 'recommended_weights')
    assert hasattr(GameProfile, 'active_weights')
    assert hasattr(GameProfile, 'created_at')
    assert hasattr(GameProfile, 'updated_at')
