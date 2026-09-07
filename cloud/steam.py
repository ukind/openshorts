"""Steam integration for OpenShorts Game Profiles.

This module handles Steam game search and metadata retrieval to populate
GameProfile with Steam information without requiring API keys.
"""

import re
import time
from typing import List, Optional, Dict, Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel


class SteamSearchResult(BaseModel):
    """Represents a Steam game search result."""
    app_id: int
    name: str
    url: str


class SteamGameMetadata(BaseModel):
    """Represents normalized Steam game metadata."""
    app_id: int
    name: str
    description: Optional[str] = None
    genres: List[str] = []
    categories: List[str] = []
    tags: List[str] = []


async def search_games(query: str, max_results: int = 10) -> List[SteamSearchResult]:
    """
    Search for games on Steam by title.

    Args:
        query: Game title to search for
        max_results: Maximum number of results to return

    Returns:
        List of Steam search results

    Raises:
        httpx.RequestError: If network request fails
        ValueError: If query is invalid
    """
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")

    # Trim and limit query length
    query = query.strip()[:100]

    # Construct search URL with English localization
    encoded_query = quote(query)
    url = f"https://store.steampowered.com/search/?term={encoded_query}&l=english"

    # Use a reasonable User-Agent to avoid being blocked
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    # Use httpx with timeout for robustness
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            # Parse HTML results
            soup = BeautifulSoup(response.text, 'html.parser')
            search_results = []

            # Find all search result rows with app IDs
            result_elements = soup.select('a.search_result_row[data-ds-appid]')

            for element in result_elements[:max_results]:
                try:
                    app_id = int(element.get('data-ds-appid', 0))
                    if app_id <= 0:
                        continue

                    # Extract title from the search name div
                    title_element = element.select_one('.search_name .title')
                    title = title_element.get_text(strip=True) if title_element else ""

                    # Extract URL
                    url = element.get('href', '')

                    if app_id and title and url:
                        search_results.append(SteamSearchResult(
                            app_id=app_id,
                            name=title,
                            url=url
                        ))
                except (ValueError, AttributeError):
                    # Skip malformed results
                    continue

            return search_results

        except httpx.RequestError as e:
            raise httpx.RequestError(f"Failed to search Steam: {str(e)}")


async def get_game_metadata(app_id: int) -> SteamGameMetadata:
    """
    Retrieve detailed metadata for a Steam game.

    Args:
        app_id: Steam App ID

    Returns:
        Normalized game metadata

    Raises:
        httpx.RequestError: If network request fails
        ValueError: If app_id is invalid
    """
    if app_id <= 0:
        raise ValueError("Invalid Steam App ID")

    # Construct app details URL with English localization
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            # Parse JSON response
            data = response.json()

            if str(app_id) not in data or not data[str(app_id)].get('success'):
                raise ValueError(f"Failed to retrieve metadata for app {app_id}")

            app_info = data[str(app_id)]['data']

            # Extract basic information
            name = app_info.get('name', '')
            description = app_info.get('short_description') or app_info.get('detailed_description', '')

            # Extract genres
            genres = []
            if 'genres' in app_info:
                genres = [genre.get('description', '') for genre in app_info['genres'] if genre.get('description')]

            # Extract categories
            categories = []
            if 'categories' in app_info:
                categories = [category.get('description', '') for category in app_info['categories'] if category.get('description')]

            # Extract tags - first try from appdetails, then fallback to product page
            tags = _extract_tags_from_app_info(app_info)

            # If no tags found in appdetails, fetch from product page (non-fatal if age-gated)
            if not tags:
                try:
                    tags = await get_game_tags_from_product_page(app_id)
                except Exception:
                    tags = []

            return SteamGameMetadata(
                app_id=app_id,
                name=name,
                description=description,
                genres=genres,
                categories=categories,
                tags=tags
            )

        except httpx.RequestError as e:
            raise httpx.RequestError(f"Failed to retrieve Steam metadata: {str(e)}")
        except (ValueError, KeyError) as e:
            raise ValueError(f"Failed to parse Steam response: {str(e)}")


def _extract_tags_from_app_info(app_info: Dict[str, Any]) -> List[str]:
    """Extract tags from app info, preferring structured data."""
    # Try to get tags from rgPackages if available
    try:
        if 'rgPackages' in app_info and 'tags' in app_info['rgPackages']:
            return app_info['rgPackages']['tags']
    except (KeyError, TypeError):
        pass

    # Also check for direct tags field
    if 'tags' in app_info:
        # Handle different tag structures - could be list of strings or list of objects
        tags = app_info['tags']
        if isinstance(tags, list):
            # If it's a list of objects with 'name' fields, extract those
            if len(tags) > 0 and isinstance(tags[0], dict) and 'name' in tags[0]:
                return [tag['name'] for tag in tags if 'name' in tag]
            # If it's already a list of strings
            elif all(isinstance(tag, str) for tag in tags):
                return tags
        elif isinstance(tags, str):
            # Handle case where tags is a single string
            return [tags]

    # Return empty list if no tags found in structured data
    return []


async def get_game_tags_from_product_page(app_id: int) -> List[str]:
    """
    Retrieve tags from the Steam product page (fallback method).

    Args:
        app_id: Steam App ID

    Returns:
        List of tag names

    Raises:
        httpx.RequestError: If network request fails
    """
    # Construct product page URL with English localization
    url = f"https://store.steampowered.com/app/{app_id}/?l=english"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            cookies = {"birthtime": "0", "mature_content": "1", "lastagecheckage": "1-January-2000"}
            response = await client.get(url, headers=headers, cookies=cookies)
            response.raise_for_status()

            # Parse HTML to extract tags
            soup = BeautifulSoup(response.text, 'html.parser')

            # Look for app_tag elements directly (this is the verified approach)
            tag_elements = soup.select('a.app_tag')
            tags = [tag.get_text(strip=True) for tag in tag_elements if tag.get_text(strip=True)]

            return tags

        except httpx.RequestError as e:
            raise httpx.RequestError(f"Failed to retrieve Steam product page: {str(e)}")
