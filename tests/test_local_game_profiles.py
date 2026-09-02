"""
Unit tests for the local GameProfile repository.
"""

import os
import json
import tempfile
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

# Import the local game profile repository
import sys
sys.path.insert(0, '.')

from cloud.local_game_profiles import (
    LocalGameProfileRepository,
    _ensure_profiles_dir,
    _is_valid_uuid,
    _safe_filename,
    _atomic_write_json
)


class TestLocalGameProfileRepository(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.original_profiles_dir = os.environ.get('LOCAL_PROFILES_DIR')
        os.environ['LOCAL_PROFILES_DIR'] = self.test_dir

        # Initialize the repository with test directory
        self.repo = LocalGameProfileRepository()

    def tearDown(self):
        """Clean up test fixtures."""
        # Restore original environment
        if self.original_profiles_dir is not None:
            os.environ['LOCAL_PROFILES_DIR'] = self.original_profiles_dir
        else:
            os.environ.pop('LOCAL_PROFILES_DIR', None)

        # Remove test directory
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def test_ensure_profiles_dir(self):
        """Test that profiles directory is created."""
        # This should not raise an exception
        _ensure_profiles_dir()

        # Directory should exist
        self.assertTrue(os.path.exists("profiles"))

        # Clean up for next test (but don't delete if it's not empty)
        try:
            os.rmdir("profiles")
        except OSError:
            pass

    def test_is_valid_uuid(self):
        """Test UUID validation."""
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        invalid_uuid = "not-a-uuid"
        path_traversal = "../../../etc/passwd"

        self.assertTrue(_is_valid_uuid(valid_uuid))
        self.assertFalse(_is_valid_uuid(invalid_uuid))
        self.assertFalse(_is_valid_uuid(path_traversal))

    def test_safe_filename(self):
        """Test safe filename generation."""
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        invalid_uuid = "not-a-uuid"

        # Valid UUID should produce correct filename
        filename = _safe_filename(valid_uuid)
        self.assertEqual(filename, "550e8400-e29b-41d4-a716-446655440000.json")

        # Invalid UUID should raise ValueError
        with self.assertRaises(ValueError):
            _safe_filename(invalid_uuid)

    def test_atomic_write_json(self):
        """Test atomic JSON writing."""
        test_data = {"name": "test", "value": 42}
        test_file = Path(self.test_dir) / "test.json"

        # Should succeed
        success = _atomic_write_json(test_file, test_data)
        self.assertTrue(success)

        # File should exist and contain correct data
        with open(test_file, 'r') as f:
            content = json.load(f)
            self.assertEqual(content, test_data)

    def test_create_profile(self):
        """Test creating a new profile."""
        profile_data = {
            "name": "Test Profile",
            "game_title": "Test Game",
            "steam_app_id": 12345,
            "active_weights": {"surprise": 1.0, "tension": 0.8}
        }

        profile_id = self.repo.create_profile(profile_data)

        # Should return a valid UUID
        self.assertTrue(_is_valid_uuid(profile_id))

        # Profile should exist and contain correct data
        retrieved = self.repo.get_profile(profile_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Test Profile")
        self.assertEqual(retrieved["game_title"], "Test Game")
        self.assertEqual(retrieved["steam_app_id"], 12345)
        self.assertEqual(retrieved["active_weights"], {"surprise": 1.0, "tension": 0.8})

        # Should have timestamps
        self.assertIn("created_at", retrieved)
        self.assertIn("updated_at", retrieved)

    def test_list_profiles(self):
        """Test listing profiles."""
        # Create two profiles
        profile1_data = {"name": "Profile 1", "game_title": "Game 1"}
        profile2_data = {"name": "Profile 2", "game_title": "Game 2"}

        id1 = self.repo.create_profile(profile1_data)
        id2 = self.repo.create_profile(profile2_data)

        # List should return both
        profiles = self.repo.list_profiles()
        # Note: there may be other test files in the directory, so just check that we have at least 2
        self.assertGreaterEqual(len(profiles), 2)

        # Should contain expected data (order may vary)
        profile_names = [p["name"] for p in profiles]
        self.assertIn("Profile 1", profile_names)
        self.assertIn("Profile 2", profile_names)

    def test_get_profile(self):
        """Test getting a specific profile."""
        profile_data = {"name": "Test Profile", "game_title": "Test Game"}
        profile_id = self.repo.create_profile(profile_data)

        # Should retrieve the profile
        retrieved = self.repo.get_profile(profile_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Test Profile")

        # Should return None for non-existent profile
        fake_id = "550e8400-e29b-41d4-a716-446655440001"
        retrieved = self.repo.get_profile(fake_id)
        self.assertIsNone(retrieved)

        # Should return None for invalid UUID
        retrieved = self.repo.get_profile("invalid-uuid")
        self.assertIsNone(retrieved)

    def test_update_profile(self):
        """Test updating a profile."""
        profile_data = {"name": "Original", "game_title": "Game"}
        profile_id = self.repo.create_profile(profile_data)

        # Update the profile
        update_data = {"name": "Updated", "game_title": "Updated Game"}
        success = self.repo.update_profile(profile_id, update_data)

        # Should succeed
        self.assertTrue(success)

        # Should have updated data
        retrieved = self.repo.get_profile(profile_id)
        self.assertEqual(retrieved["name"], "Updated")
        self.assertEqual(retrieved["game_title"], "Updated Game")

        # Updated_at should be newer
        self.assertIn("updated_at", retrieved)

    def test_delete_profile(self):
        """Test deleting a profile."""
        profile_data = {"name": "To Delete", "game_title": "Test"}
        profile_id = self.repo.create_profile(profile_data)

        # Should exist before deletion
        retrieved = self.repo.get_profile(profile_id)
        self.assertIsNotNone(retrieved)

        # Delete should succeed
        success = self.repo.delete_profile(profile_id)
        self.assertTrue(success)

        # Should not exist after deletion
        retrieved = self.repo.get_profile(profile_id)
        self.assertIsNone(retrieved)

    def test_duplicate_profile(self):
        """Test duplicating a profile."""
        original_data = {
            "name": "Original Profile",
            "game_title": "Test Game",
            "active_weights": {"surprise": 1.0}
        }
        original_id = self.repo.create_profile(original_data)

        # Duplicate should succeed
        new_id = self.repo.duplicate_profile(original_id)
        self.assertIsNotNone(new_id)
        self.assertTrue(_is_valid_uuid(new_id))

        # Both profiles should exist
        original = self.repo.get_profile(original_id)
        duplicate = self.repo.get_profile(new_id)

        self.assertIsNotNone(original)
        self.assertIsNotNone(duplicate)

        # Should have same data but different IDs
        self.assertEqual(original["name"], duplicate["name"])
        self.assertEqual(original["game_title"], duplicate["game_title"])
        self.assertEqual(original["active_weights"], duplicate["active_weights"])
        self.assertNotEqual(original["id"], duplicate["id"])

    def test_malformed_json_handling(self):
        """Test handling of malformed JSON files."""
        # Create a malformed JSON file
        malformed_file = Path(self.test_dir) / "malformed.json"
        with open(malformed_file, 'w') as f:
            f.write("this is not valid json {")

        # Should not crash when listing profiles
        profiles = self.repo.list_profiles()
        # Should return empty list or skip malformed file
        self.assertIsInstance(profiles, list)

        # Clean up
        malformed_file.unlink()

    def test_path_traversal_protection(self):
        """Test protection against path traversal attacks."""
        # This should not create a file outside the profiles directory
        with self.assertRaises(ValueError):
            _safe_filename("../../etc/passwd")

        # Should not be able to access non-UUID strings as filenames
        with self.assertRaises(ValueError):
            _safe_filename("../../../etc/passwd")

    def test_invalid_profile_id(self):
        """Test handling of invalid profile IDs."""
        # These should all return None or False without crashing
        result = self.repo.get_profile("not-a-uuid")
        self.assertIsNone(result)

        result = self.repo.update_profile("not-a-uuid", {})
        self.assertFalse(result)

        result = self.repo.delete_profile("not-a-uuid")
        self.assertFalse(result)

        result = self.repo.duplicate_profile("not-a-uuid")
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
