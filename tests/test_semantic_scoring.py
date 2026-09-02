"""
Unit tests for semantic scoring layer.
"""

import unittest
from typing import List
from cloud.semantic_scoring import (
    SemanticSignal,
    calculate_semantic_score,
    calculate_profile_modifier,
    apply_game_profile_scoring
)


class TestSemanticScoring(unittest.TestCase):

    def test_no_profile_returns_existing_score(self):
        """Test that no profile preserves existing score exactly."""
        existing_score = 100.0
        result = apply_game_profile_scoring(existing_score, None, [])
        self.assertEqual(result, existing_score)

    def test_empty_weights_returns_existing_score(self):
        """Test that empty weights preserve existing score."""
        existing_score = 50.0
        result = apply_game_profile_scoring(existing_score, {}, [])
        self.assertEqual(result, existing_score)

    def test_all_weights_zero_returns_existing_score(self):
        """Test that all zero weights preserve existing score."""
        existing_score = 75.0
        active_weights = {"emotional_reaction": 0.0, "surprise": 0.0}
        result = apply_game_profile_scoring(existing_score, active_weights, [])
        self.assertEqual(result, existing_score)

    def test_no_detected_signals_returns_existing_score(self):
        """Test that no detected signals preserve existing score."""
        existing_score = 80.0
        active_weights = {"emotional_reaction": 1.0}
        result = apply_game_profile_scoring(existing_score, active_weights, [])
        self.assertEqual(result, existing_score)

    def test_single_category(self):
        """Test single category calculation."""
        existing_score = 100.0
        active_weights = {"emotional_reaction": 1.0}
        signals = [SemanticSignal(category="emotional_reaction", score=0.8, confidence=0.9)]

        result = apply_game_profile_scoring(existing_score, active_weights, signals)
        # semantic_score = 0.8, modifier = 0.8 * 0.5 = 0.4
        # final = 100 * (1 + 0.4) = 140.0
        self.assertEqual(result, 140.0)

    def test_multiple_categories(self):
        """Test multiple categories with weighted average."""
        existing_score = 100.0
        active_weights = {"emotional_reaction": 1.0, "surprise": 0.5}
        signals = [
            SemanticSignal(category="emotional_reaction", score=0.8, confidence=0.9),
            SemanticSignal(category="surprise", score=0.6, confidence=0.8)
        ]

        result = apply_game_profile_scoring(existing_score, active_weights, signals)
        # semantic_score = (0.8 * 1.0 + 0.6 * 0.5) / (1.0 + 0.5) = 1.1 / 1.5 = 0.733...
        # modifier = 0.733... * 0.5 = 0.366...
        # final = 100 * (1 + 0.366...) = 136.6...
        expected = 100 * (1 + (0.8 * 1.0 + 0.6 * 0.5) / (1.0 + 0.5) * 0.5)
        self.assertAlmostEqual(result, expected, places=2)

    def test_missing_category(self):
        """Test that missing categories don't penalize clips."""
        existing_score = 100.0
        active_weights = {"emotional_reaction": 1.0, "surprise": 1.0}  # Both have weights
        signals = [SemanticSignal(category="emotional_reaction", score=0.8, confidence=0.9)]
        # surprise category is missing but has weight - should not penalize

        result = apply_game_profile_scoring(existing_score, active_weights, signals)
        # semantic_score = (0.8 * 1.0) / (1.0) = 0.8
        # modifier = 0.8 * 0.5 = 0.4
        # final = 100 * (1 + 0.4) = 140.0
        expected = 100 * (1 + 0.8 * 0.5)
        self.assertEqual(result, expected)

    def test_maximum_semantic_score(self):
        """Test maximum semantic score behavior."""
        existing_score = 100.0
        active_weights = {"emotional_reaction": 1.0}
        signals = [SemanticSignal(category="emotional_reaction", score=1.0, confidence=1.0)]

        result = apply_game_profile_scoring(existing_score, active_weights, signals)
        # semantic_score = 1.0, modifier = 1.0 * 0.5 = 0.5
        # final = 100 * (1 + 0.5) = 150.0
        self.assertEqual(result, 150.0)

    def test_score_bounds(self):
        """Test that semantic scores remain bounded."""
        existing_score = 100.0
        active_weights = {"emotional_reaction": 2.0}  # High weight
        signals = [SemanticSignal(category="emotional_reaction", score=1.0, confidence=1.0)]

        result = apply_game_profile_scoring(existing_score, active_weights, signals)
        # semantic_score should be capped at 1.0 due to normalization
        # final = 100 * (1 + 0.5) = 150.0 (since normalized score = 1.0)
        self.assertEqual(result, 150.0)

    def test_calculate_semantic_score_basic(self):
        """Test basic semantic score calculation."""
        active_weights = {"emotional_reaction": 1.0}
        signals = [SemanticSignal(category="emotional_reaction", score=0.8, confidence=0.9)]

        result = calculate_semantic_score(active_weights, signals)
        self.assertEqual(result, 0.8)

    def test_calculate_semantic_score_multiple_categories(self):
        """Test semantic score with multiple categories."""
        active_weights = {"emotional_reaction": 1.0, "surprise": 0.5}
        signals = [
            SemanticSignal(category="emotional_reaction", score=0.8, confidence=0.9),
            SemanticSignal(category="surprise", score=0.6, confidence=0.8)
        ]

        result = calculate_semantic_score(active_weights, signals)
        # (0.8 * 1.0 + 0.6 * 0.5) / (1.0 + 0.5) = 1.1 / 1.5 = 0.733...
        expected = (0.8 * 1.0 + 0.6 * 0.5) / (1.0 + 0.5)
        self.assertAlmostEqual(result, expected, places=2)

    def test_calculate_profile_modifier(self):
        """Test profile modifier calculation."""
        # Test with maximum influence
        result = calculate_profile_modifier(1.0)
        self.assertEqual(result, 0.5)  # 1.0 * 0.5

        # Test with zero score
        result = calculate_profile_modifier(0.0)
        self.assertEqual(result, 0.0)

        # Test with intermediate value
        result = calculate_profile_modifier(0.8)
        self.assertEqual(result, 0.4)  # 0.8 * 0.5


if __name__ == '__main__':
    unittest.main()
