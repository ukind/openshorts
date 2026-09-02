"""
Unit tests for the semantic analyzer module.
"""

import unittest
from unittest.mock import Mock, patch
from typing import List, Dict, Any

# Import our modules
from cloud.semantic_analyzer import (
    SemanticSignal,
    SemanticSignalsResponse,
    extract_frames_from_window,
    analyze_semantic_window,
    _build_multimodal_prompt
)
from ai_provider import AIProvider


class TestSemanticSignal(unittest.TestCase):
    """Test SemanticSignal model validation."""

    def test_valid_signal(self):
        """Test that valid signals are accepted."""
        signal = SemanticSignal(
            category="emotional_reaction",
            score=0.8,
            confidence=0.9
        )
        self.assertEqual(signal.category, "emotional_reaction")
        self.assertEqual(signal.score, 0.8)
        self.assertEqual(signal.confidence, 0.9)

    def test_invalid_category(self):
        """Test that invalid categories are rejected."""
        with self.assertRaises(Exception):  # Pydantic validation error
            SemanticSignal(
                category="invalid_category",
                score=0.8,
                confidence=0.9
            )

    def test_score_out_of_range(self):
        """Test that scores outside 0.0-1.0 are rejected."""
        with self.assertRaises(Exception):  # Pydantic validation error
            SemanticSignal(
                category="emotional_reaction",
                score=1.5,
                confidence=0.9
            )

    def test_confidence_out_of_range(self):
        """Test that confidences outside 0.0-1.0 are rejected."""
        with self.assertRaises(Exception):  # Pydantic validation error
            SemanticSignal(
                category="emotional_reaction",
                score=0.8,
                confidence=1.5
            )


class TestSemanticSignalsResponse(unittest.TestCase):
    """Test SemanticSignalsResponse model."""

    def test_valid_response(self):
        """Test that valid responses are accepted."""
        signals = [
            SemanticSignal(category="emotional_reaction", score=0.8, confidence=0.9),
            SemanticSignal(category="surprise", score=0.7, confidence=0.8)
        ]
        response = SemanticSignalsResponse(signals=signals)
        self.assertEqual(len(response.signals), 2)
        self.assertEqual(response.signals[0].category, "emotional_reaction")


class TestFrameExtraction(unittest.TestCase):
    """Test frame extraction functionality."""

    @patch('cv2.VideoCapture')
    def test_extract_frames_normal_window(self, mock_cap):
        """Test frame extraction from a normal window."""
        # Setup mock
        mock_video = Mock()
        mock_cap.return_value = mock_video

        # Mock video properties
        mock_video.isOpened.return_value = True
        mock_video.get.side_effect = [30.0, 1000]  # fps, frame count

        # Mock frame reading
        mock_frame = Mock()
        mock_frame.shape = (720, 1280, 3)
        mock_video.read.return_value = (True, mock_frame)

        # Mock encoding
        with patch('cv2.imencode') as mock_encode:
            mock_encode.return_value = (True, b"fake_jpeg_data")

            frames = extract_frames_from_window(
                video_path="test.mp4",
                window_start=10.0,
                window_end=20.0,
                num_frames=3
            )

            # Should return 3 frames
            self.assertEqual(len(frames), 3)
            self.assertEqual(frames[0]["frame_index"], 0)
            self.assertIn("data:image/jpeg;base64,", frames[0]["base64_image"])

    @patch('cv2.VideoCapture')
    def test_extract_frames_short_window(self, mock_cap):
        """Test frame extraction from a very short window."""
        # Setup mock
        mock_video = Mock()
        mock_cap.return_value = mock_video

        # Mock video properties
        mock_video.isOpened.return_value = True
        mock_video.get.side_effect = [30.0, 1000]  # fps, frame count

        # Mock frame reading
        mock_frame = Mock()
        mock_frame.shape = (720, 1280, 3)
        mock_video.read.return_value = (True, mock_frame)

        # Mock encoding
        with patch('cv2.imencode') as mock_encode:
            mock_encode.return_value = (True, b"fake_jpeg_data")

            frames = extract_frames_from_window(
                video_path="test.mp4",
                window_start=10.0,
                window_end=10.01,  # Very short window
                num_frames=3
            )

            # Should return at least one frame (with fallback logic)
            self.assertGreaterEqual(len(frames), 1)


class TestMultimodalPrompt(unittest.TestCase):
    """Test multimodal prompt building."""

    def test_build_prompt(self):
        """Test that the prompt is built correctly."""
        transcript = "This is a test transcript."
        frames = [{"frame_index": 0, "base64_image": "data:image/jpeg;base64,test"}]
        game_profile_context = {
            "game_type": "horror",
            "gameplay_characteristics": ["multiplayer", "co-op"],
            "key_moments": ["jump scare", "group decision"]
        }

        prompt = _build_multimodal_prompt(transcript, frames, game_profile_context)

        # Check that key elements are present
        self.assertIn("This is a test transcript", prompt)
        self.assertIn("Game Type: horror", prompt)
        self.assertIn("multiplayer", prompt)
        self.assertIn("jump scare", prompt)


class TestSemanticAnalyzerIntegration(unittest.TestCase):
    """Test integration of semantic analyzer components."""

    def test_analyze_semantic_window_success(self):
        """Test successful analysis with mocked provider."""
        # Create mock provider that returns valid response
        mock_provider = Mock(spec=AIProvider)

        # Mock the generate_content method to return a valid response
        mock_response = {
            "response": {
                "signals": [
                    {
                        "category": "emotional_reaction",
                        "score": 0.8,
                        "confidence": 0.9
                    }
                ]
            },
            "cost_analysis": None
        }
        mock_provider.generate_content.return_value = mock_response

        # Test with minimal inputs
        transcript = "Test transcript"
        frames = [{"frame_index": 0, "base64_image": "data:image/jpeg;base64,test"}]
        game_profile_context = {
            "game_type": "horror",
            "gameplay_characteristics": [],
            "key_moments": []
        }

        result = analyze_semantic_window(transcript, frames, game_profile_context, mock_provider)

        # Should return a valid response
        self.assertIsInstance(result, SemanticSignalsResponse)
        self.assertEqual(len(result.signals), 1)
        self.assertEqual(result.signals[0].category, "emotional_reaction")

    def test_analyze_semantic_window_failure(self):
        """Test analysis failure returns empty signals."""
        # Create mock provider that raises an exception
        mock_provider = Mock(spec=AIProvider)
        mock_provider.generate_content.side_effect = Exception("API Error")

        transcript = "Test transcript"
        frames = [{"frame_index": 0, "base64_image": "data:image/jpeg;base64,test"}]
        game_profile_context = {
            "game_type": "horror",
            "gameplay_characteristics": [],
            "key_moments": []
        }

        result = analyze_semantic_window(transcript, frames, game_profile_context, mock_provider)

        # Should return empty signals on failure
        self.assertIsInstance(result, SemanticSignalsResponse)
        self.assertEqual(len(result.signals), 0)

    def test_multimodal_content_sent_correctly(self):
        """Test that multimodal content is properly constructed and sent to provider."""
        # Create mock provider that captures the call arguments
        mock_provider = Mock(spec=AIProvider)

        # Mock the generate_content method to capture what's passed to it
        def side_effect(prompt, schema=None, messages=None, **kwargs):
            # Verify that messages parameter is passed with correct structure
            self.assertIsNotNone(messages)
            self.assertIsInstance(messages, list)
            self.assertGreater(len(messages), 0)

            # Check that we have text and image content
            text_content = None
            image_content = None
            for msg in messages:
                if msg.get("type") == "text":
                    text_content = msg
                elif msg.get("type") == "image_url":
                    image_content = msg

            self.assertIsNotNone(text_content)
            self.assertIsNotNone(image_content)
            self.assertIn("data:image/jpeg;base64,test", image_content["image_url"]["url"])

            # Return a valid response to avoid failure in test
            return {
                "response": {"signals": []},
                "cost_analysis": None
            }

        mock_provider.generate_content.side_effect = side_effect

        # Test with minimal inputs
        transcript = "Test transcript"
        frames = [{"frame_index": 0, "base64_image": "data:image/jpeg;base64,test"}]
        game_profile_context = {
            "game_type": "horror",
            "gameplay_characteristics": [],
            "key_moments": []
        }

        result = analyze_semantic_window(transcript, frames, game_profile_context, mock_provider)

        # Should return a valid response
        self.assertIsInstance(result, SemanticSignalsResponse)
        self.assertEqual(len(result.signals), 0)


if __name__ == '__main__':
    unittest.main()
