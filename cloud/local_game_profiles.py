"""
Local JSON-backed GameProfile repository for OpenShorts.

This module provides a local file-based storage implementation for GameProfiles
that can be used in self-hosted installations without PostgreSQL.
"""

import os
import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import tempfile
import shutil

# Local storage directory
LOCAL_PROFILES_DIR = "profiles"
LOCAL_PROFILES_PATH = Path(LOCAL_PROFILES_DIR)

def _ensure_profiles_dir():
    """Ensure the profiles directory exists."""
    LOCAL_PROFILES_PATH.mkdir(exist_ok=True)

def _is_valid_uuid(uuid_str: str) -> bool:
    """Validate that a string is a valid UUID4."""
    try:
        uuid_obj = uuid.UUID(uuid_str)
        return str(uuid_obj).lower() == uuid_str.lower()
    except (ValueError, TypeError):
        return False

def _safe_filename(profile_id: str) -> str:
    """Safely convert profile ID to filename, preventing path traversal."""
    if not _is_valid_uuid(profile_id):
        raise ValueError(f"Invalid profile ID: {profile_id}")

    # Ensure we're only dealing with UUIDs and not paths
    safe_id = profile_id.replace("/", "").replace("\\", "").replace("..", "")
    return f"{safe_id}.json"

def _atomic_write_json(filepath: Path, data: Dict[str, Any]) -> bool:
    """Write JSON data atomically using temporary file + rename."""
    try:
        # Create a temporary file in the same directory
        temp_dir = filepath.parent
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tmp', dir=temp_dir, delete=False) as tmp_file:
            json.dump(data, tmp_file, indent=2, ensure_ascii=False)
            tmp_filename = tmp_file.name

        # Atomically replace the target file
        os.replace(tmp_filename, filepath)
        return True
    except Exception:
        # Clean up temp file if something goes wrong
        try:
            os.unlink(tmp_filename)
        except:
            pass
        return False

class LocalGameProfileRepository:
    """Local JSON-backed repository for GameProfiles."""

    def __init__(self):
        _ensure_profiles_dir()

    def create_profile(self, profile_data: Dict[str, Any]) -> str:
        """
        Create a new profile and return its ID.

        Args:
            profile_data: Dictionary containing profile fields

        Returns:
            The UUID of the created profile
        """
        # Generate a new UUID for this profile
        profile_id = str(uuid.uuid4())

        # Add required timestamps if not present
        now = datetime.now().isoformat()
        profile_data["id"] = profile_id
        profile_data["created_at"] = profile_data.get("created_at", now)
        profile_data["updated_at"] = profile_data.get("updated_at", now)

        # Remove user_id for local mode (not needed)
        profile_data.pop("user_id", None)

        # Write to file atomically
        filename = _safe_filename(profile_id)
        filepath = LOCAL_PROFILES_PATH / filename

        if not _atomic_write_json(filepath, profile_data):
            raise IOError(f"Failed to write profile file: {filepath}")

        return profile_id

    def list_profiles(self) -> List[Dict[str, Any]]:
        """
        List all profiles.

        Returns:
            List of profile dictionaries
        """
        profiles = []

        # Iterate through all .json files in the profiles directory
        for json_file in LOCAL_PROFILES_PATH.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    profile_data = json.load(f)
                    # Remove user_id if present (for compatibility)
                    profile_data.pop("user_id", None)

                    # Ensure the profile has an ID field
                    if "id" not in profile_data:
                        # Derive ID from filename if it doesn't exist
                        profile_data["id"] = json_file.stem

                    profiles.append(profile_data)
            except (json.JSONDecodeError, IOError):
                # Skip malformed or unreadable files
                continue

        # Sort by created_at to provide deterministic ordering
        profiles.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return profiles

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific profile by ID.

        Args:
            profile_id: UUID of the profile to retrieve

        Returns:
            Profile dictionary or None if not found
        """
        if not _is_valid_uuid(profile_id):
            return None

        filename = _safe_filename(profile_id)
        filepath = LOCAL_PROFILES_PATH / filename

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                profile_data = json.load(f)
                # Remove user_id if present (for compatibility)
                profile_data.pop("user_id", None)

                # Ensure the profile has an ID field
                if "id" not in profile_data:
                    profile_data["id"] = profile_id

                return profile_data
        except (json.JSONDecodeError, IOError):
            return None

    def update_profile(self, profile_id: str, profile_data: Dict[str, Any]) -> bool:
        """
        Update a profile and return success status.
        Merges with existing file so partial updates (e.g. Steam lookup
        without custom_description) don't drop fields.
        """
        if not _is_valid_uuid(profile_id):
            return False

        filename = _safe_filename(profile_id)
        filepath = LOCAL_PROFILES_PATH / filename
        try:
            # Merge with existing to preserve omitted fields (e.g. custom_description)
            existing = {}
            if filepath.exists():
                try:
                    import json
                    with open(filepath, 'r', encoding='utf-8') as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}
            merged = {**existing, **profile_data}
            merged["updated_at"] = datetime.now().isoformat()
            merged["id"] = profile_id
            merged.pop("user_id", None)
            # Preserve created_at from existing
            if "created_at" in existing and "created_at" not in profile_data:
                merged["created_at"] = existing["created_at"]
            return _atomic_write_json(filepath, merged)
        except Exception:
            return False

    def delete_profile(self, profile_id: str) -> bool:
        """
        Delete a profile and return success status.

        Args:
            profile_id: UUID of the profile to delete

        Returns:
            True if successful, False otherwise
        """
        if not _is_valid_uuid(profile_id):
            return False

        filename = _safe_filename(profile_id)
        filepath = LOCAL_PROFILES_PATH / filename

        try:
            if filepath.exists():
                filepath.unlink()
                return True
            return False
        except Exception:
            return False

    def duplicate_profile(self, profile_id: str) -> Optional[str]:
        """
        Duplicate a profile and return new ID.

        Args:
            profile_id: UUID of the profile to duplicate

        Returns:
            New profile ID or None if failed
        """
        if not _is_valid_uuid(profile_id):
            return None

        # Get the original profile
        original = self.get_profile(profile_id)
        if not original:
            return None

        # Create new profile with same data but different ID
        # Remove old ID and timestamps to avoid conflicts
        new_data = original.copy()
        new_data.pop("id", None)
        new_data.pop("created_at", None)
        new_data.pop("updated_at", None)

        # Create the new profile
        return self.create_profile(new_data)
