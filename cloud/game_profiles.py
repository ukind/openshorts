"""Game profile management for OpenShorts.

This module handles CRUD operations for GameProfile entities and provides
functions to work with game profiles in the clip generation pipeline.
"""

from typing import Optional, List, Dict, Any
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError

from . import database
from .models import GameProfile

async def create_game_profile(user_id: str, profile_data: Dict[str, Any]) -> GameProfile:
    """Create a new game profile for a user."""
    # Check if local mode is enabled
    import os
    if os.environ.get('LOCAL_GAMEPROFILES') == '1':
        from .local_game_profiles import LocalGameProfileRepository
        repo = LocalGameProfileRepository()
        profile_id = repo.create_profile(profile_data)
        # Return a minimal representation for compatibility
        return type('GameProfile', (), {'id': profile_id})()

    # Only allow specific fields to be set
    allowed_fields = {
        'name', 'game_title', 'steam_app_id', 'steam_description', 'custom_description',
        'steam_genres', 'steam_tags', 'custom_description', 'ai_analysis', 'recommended_weights',
        'active_weights'
    }
    filtered_data = {k: v for k, v in profile_data.items() if k in allowed_fields}

    async with database.session() as session:
        async with session.begin():
            profile = GameProfile(
                user_id=user_id,
                **profile_data
            )
            session.add(profile)
            await session.flush()
            return profile


async def list_game_profiles(user_id: str) -> List[GameProfile]:
    """List all game profiles for a user."""
    # Check if local mode is enabled
    import os
    if os.environ.get('LOCAL_GAMEPROFILES') == '1':
        from .local_game_profiles import LocalGameProfileRepository
        repo = LocalGameProfileRepository()
        # Return list of dictionaries for compatibility
        return [type('GameProfile', (), p)() for p in repo.list_profiles()]

    async with database.session() as session:
        stmt = select(GameProfile).where(GameProfile.user_id == user_id).order_by(GameProfile.created_at.desc())
        result = await session.execute(stmt)
        return result.scalars().all()


async def get_game_profile(user_id: str, profile_id: str) -> Optional[GameProfile]:
    """Get a specific game profile for a user."""
    # Check if local mode is enabled
    import os
    if os.environ.get('LOCAL_GAMEPROFILES') == '1':
        from .local_game_profiles import LocalGameProfileRepository
        repo = LocalGameProfileRepository()
        profile_data = repo.get_profile(profile_id)
        if not profile_data:
            return None
        # Return a minimal representation for compatibility
        return type('GameProfile', (), profile_data)()

    async with database.session() as session:
        stmt = select(GameProfile).where(
            GameProfile.user_id == user_id,
            GameProfile.id == profile_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def update_game_profile(user_id: str, profile_id: str, profile_data: Dict[str, Any]) -> Optional[GameProfile]:
    """Update a game profile for a user."""
    # Check if local mode is enabled
    import os
    if os.environ.get('LOCAL_GAMEPROFILES') == '1':
        from .local_game_profiles import LocalGameProfileRepository
        repo = LocalGameProfileRepository()
        success = repo.update_profile(profile_id, profile_data)
        if not success:
            return None
        # Return a minimal representation for compatibility
        return type('GameProfile', (), {'id': profile_id})()

    # Only allow specific fields to be set
    allowed_fields = {
        'name', 'game_title', 'steam_app_id', 'steam_description', 'custom_description',
        'steam_genres', 'steam_tags', 'ai_analysis', 'recommended_weights',
        'active_weights'
    }
    filtered_data = {k: v for k, v in profile_data.items() if k in allowed_fields}

    async with database.session() as session:
        async with session.begin():
            stmt = update(GameProfile).where(
                GameProfile.user_id == user_id,
                GameProfile.id == profile_id
            ).values(**filtered_data)
            result = await session.execute(stmt)

            if result.rowcount == 0:
                return None

            # Get the updated profile
            stmt = select(GameProfile).where(
                GameProfile.user_id == user_id,
                GameProfile.id == profile_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()


async def delete_game_profile(user_id: str, profile_id: str) -> bool:
    """Delete a game profile for a user."""
    # Check if local mode is enabled
    import os
    if os.environ.get('LOCAL_GAMEPROFILES') == '1':
        from .local_game_profiles import LocalGameProfileRepository
        repo = LocalGameProfileRepository()
        return repo.delete_profile(profile_id)

    async with database.session() as session:
        async with session.begin():
            stmt = delete(GameProfile).where(
                GameProfile.user_id == user_id,
                GameProfile.id == profile_id
            )
            result = await session.execute(stmt)
            return result.rowcount > 0


async def duplicate_game_profile(user_id: str, profile_id: str) -> Optional[GameProfile]:
    """Duplicate a game profile for a user."""
    # Check if local mode is enabled
    import os
    if os.environ.get('LOCAL_GAMEPROFILES') == '1':
        from .local_game_profiles import LocalGameProfileRepository
        repo = LocalGameProfileRepository()
        new_id = repo.duplicate_profile(profile_id)
        if not new_id:
            return None
        # Return a minimal representation for compatibility
        return type('GameProfile', (), {'id': new_id})()

    # First get the original profile
    original = await get_game_profile(user_id, profile_id)
    if not original:
        return None

    # Generate a unique name for the duplicate
    base_name = original.name
    name = f"{base_name} (copy)"

    # Check for existing duplicates with same name and generate unique name
    async with database.session() as session:
        # Count how many profiles already have this base name
        stmt = select(GameProfile).where(
            GameProfile.user_id == user_id,
            GameProfile.name.like(f"{base_name} (copy%)")
        )
        result = await session.execute(stmt)
        existing_profiles = result.scalars().all()

        # Find the highest number in existing copies
        max_number = 0
        for profile in existing_profiles:
            if profile.name.startswith(f"{base_name} (copy") and profile.name.endswith(")"):
                try:
                    # Extract number from "Game (copy 2)"
                    number_part = profile.name.split(" (")[1].split(")")[0]
                    if number_part.startswith("copy "):
                        num = int(number_part.split(" ")[1])
                        max_number = max(max_number, num)
                except (IndexError, ValueError):
                    pass

        # If we have duplicates, increment the number
        if max_number > 0:
            name = f"{base_name} (copy {max_number + 1})"

    # Create a new profile with the same data but different ID and timestamps
    new_data = {
        'name': name,
        'game_title': original.game_title,
        'steam_app_id': original.steam_app_id,
        'steam_description': original.steam_description,
        'steam_genres': original.steam_genres,
        'steam_tags': original.steam_tags,
        'ai_analysis': original.ai_analysis,
        'recommended_weights': original.recommended_weights,
        'active_weights': original.active_weights,
    }

    return await create_game_profile(user_id, new_data)
