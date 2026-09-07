"""
Semantic scoring layer for OpenShorts GameProfile integration.

This module provides the core functionality to apply GameProfile active_weights
to clip scoring while maintaining backward compatibility with existing behavior.
"""

from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field


class SemanticSignal(BaseModel):
    """Represents a detected semantic signal from transcript or vision analysis."""
    category: str
    score: float = Field(..., ge=0.0, le=1.0)  # Normalized signal strength (0.0-1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)  # Confidence level of detection (0.0-1.0)


def calculate_semantic_score(
    active_weights: Optional[Dict[str, float]],
    detected_signals: List[SemanticSignal]
) -> float:
    """
    Calculate semantic score from detected signals and active weights.

    Args:
        active_weights: Dictionary mapping category names to their relative weights (0.0-1.0)
        detected_signals: List of detected semantic signals

    Returns:
        Normalized semantic score between 0.0 and 1.0
    """
    if not detected_signals or not active_weights:
        return 0.0

    # Aggregate signals per category (select strongest signal)
    category_scores = {}
    for signal in detected_signals:
        current_score = category_scores.get(signal.category, 0.0)
        if signal.score > current_score:
            category_scores[signal.category] = signal.score

    # Calculate weighted sum
    weighted_sum = 0.0
    total_weight = 0.0

    for category, score in category_scores.items():
        weight = active_weights.get(category, 0.0)
        if weight > 0:
            weighted_sum += score * weight
            total_weight += weight

    # Normalize by total weight to prevent unbounded growth
    if total_weight > 0:
        normalized_score = weighted_sum / total_weight
        return min(normalized_score, 1.0)  # Cap at 1.0
    else:
        return 0.0


def calculate_profile_modifier(
    semantic_score: float,
    max_influence: float = 0.5
) -> float:
    """
    Calculate the profile modifier based on semantic score.

    Args:
        semantic_score: Normalized semantic score (0.0-1.0)
        max_influence: Maximum influence percentage (default 0.5 = 50%)

    Returns:
        Profile modifier value between 0.0 and max_influence
    """
    return semantic_score * max_influence


def apply_game_profile_scoring(
    existing_score: float,
    active_weights: Optional[Dict[str, float]],
    detected_signals: List[SemanticSignal]
) -> float:
    """
    Apply GameProfile scoring modifier to an existing clip score.

    If no GameProfile is provided, returns the existing score unchanged.
    Otherwise applies a bounded semantic scoring modifier.

    Args:
        existing_score: The original clip score from the existing pipeline
        active_weights: Active weights from GameProfile (can be None)
        detected_signals: List of semantic signals detected for this clip

    Returns:
        Final score with GameProfile influence applied (if applicable)
    """
    # If no profile is selected, preserve exact existing behavior
    if active_weights is None:
        return existing_score

    # Calculate semantic score from detected signals and weights
    semantic_score = calculate_semantic_score(active_weights, detected_signals)

    # Calculate profile modifier
    profile_modifier = calculate_profile_modifier(semantic_score)

    # Apply modifier to existing score
    return existing_score * (1 + profile_modifier)
