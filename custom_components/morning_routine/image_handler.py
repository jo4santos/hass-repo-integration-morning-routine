"""Image handling for Morning Routine Gamification integration."""
from __future__ import annotations

import aiofiles
import aiohttp
import base64
import logging
import os
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class ImageHandler:
    """Handle photo capture storage and AI image generation."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize image handler."""
        self.hass = hass
        self.storage_path = hass.config.path("www/morning_routine_photos")
        # Create storage directory if it doesn't exist
        os.makedirs(self.storage_path, exist_ok=True)
        _LOGGER.debug(f"Image handler initialized with storage path: {self.storage_path}")

    async def save_photo(self, child: str, photo_data: str) -> str:
        """Save a photo from camera capture.

        Args:
            child: Child name (duarte or leonor)
            photo_data: Base64 encoded photo data (without data:image/jpeg;base64, prefix)

        Returns:
            Path to saved photo file
        """
        try:
            # Decode base64 data
            photo_bytes = base64.b64decode(photo_data)

            # Generate filename with timestamp
            timestamp = dt_util.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{child}_{timestamp}.jpg"
            filepath = os.path.join(self.storage_path, filename)

            # Save file asynchronously
            async with aiofiles.open(filepath, "wb") as f:
                await f.write(photo_bytes)

            _LOGGER.info(f"Saved photo for {child}: {filepath}")
            return filepath

        except Exception as ex:
            _LOGGER.error(f"Failed to save photo for {child}: {ex}")
            raise

    async def save_audio(self, child: str, audio_data: str) -> str:
        """Save an audio recording from breakfast activity.

        Args:
            child: Child name (duarte or leonor)
            audio_data: Base64 encoded audio data (without data prefix)

        Returns:
            Path to saved audio file
        """
        try:
            # Decode base64 data
            audio_bytes = base64.b64decode(audio_data)

            # Generate filename with timestamp
            timestamp = dt_util.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{child}_breakfast_{timestamp}.webm"
            filepath = os.path.join(self.storage_path, filename)

            # Save file asynchronously
            async with aiofiles.open(filepath, "wb") as f:
                await f.write(audio_bytes)

            _LOGGER.info(f"Saved audio for {child}: {filepath}")
            return filepath

        except Exception as ex:
            _LOGGER.error(f"Failed to save audio for {child}: {ex}")
            raise

    def list_history(self, child: str) -> list[dict]:
        """List historical photos and audios for a child.

        Args:
            child: Child name (duarte or leonor)

        Returns:
            List of dictionaries with date and file paths, sorted by date (newest first)
        """
        try:
            # Get all files for this child
            files = os.listdir(self.storage_path)

            # Filter files for this child (photos and audios, not rewards)
            child_files = [
                f for f in files
                if f.startswith(f"{child}_") and not f.startswith(f"{child}_reward_")
            ]

            # Group by date
            history = {}
            for filename in child_files:
                try:
                    # Extract timestamp from filename
                    # Format: child_YYYYMMDD_HHMMSS.ext or child_breakfast_YYYYMMDD_HHMMSS.ext
                    parts = filename.replace(f"{child}_", "").replace("breakfast_", "")
                    timestamp_str = parts.split(".")[0]  # Remove extension
                    date_str = timestamp_str.split("_")[0]  # Get YYYYMMDD

                    if date_str not in history:
                        history[date_str] = {
                            "date": date_str,
                            "photo": None,
                            "audio": None,
                            "transcription": None
                        }

                    # Determine file type
                    filepath = f"/local/morning_routine_photos/{filename}"
                    if "breakfast" in filename:
                        if filename.endswith(".txt"):
                            # Transcription file
                            history[date_str]["transcription"] = filepath
                        else:
                            # Audio file
                            history[date_str]["audio"] = filepath
                    else:
                        history[date_str]["photo"] = filepath

                except Exception as ex:
                    _LOGGER.warning(f"Failed to parse filename {filename}: {ex}")
                    continue

            # Convert to list and sort by date (newest first)
            result = sorted(history.values(), key=lambda x: x["date"], reverse=True)

            _LOGGER.debug(f"Found {len(result)} historical entries for {child}")
            return result

        except Exception as ex:
            _LOGGER.error(f"Failed to list history for {child}: {ex}")
            return []

    async def download_reward_image(self, child: str, image_url: str) -> str:
        """Download AI-generated reward image from URL.

        Args:
            child: Child name (duarte or leonor)
            image_url: URL of the generated image

        Returns:
            Path to downloaded image file
        """
        try:
            # Generate filename with timestamp
            timestamp = dt_util.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{child}_reward_{timestamp}.png"
            filepath = os.path.join(self.storage_path, filename)

            # Download image
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        async with aiofiles.open(filepath, "wb") as f:
                            await f.write(image_data)
                        _LOGGER.info(f"Downloaded reward image for {child}: {filepath}")
                        return filepath
                    else:
                        _LOGGER.error(
                            f"Failed to download reward image: HTTP {response.status}"
                        )
                        raise Exception(f"HTTP {response.status}")

        except Exception as ex:
            _LOGGER.error(f"Failed to download reward image for {child}: {ex}")
            raise
