"""Google Drive uploader for Morning Routine Gamification integration."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "morning_routine_google_drive_tokens"

# OAuth 2.0 scopes
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Folder structure cache storage key
FOLDER_CACHE_KEY = "morning_routine_gdrive_folders"
FOLDER_CACHE_VERSION = 1


class GoogleDriveUploader:
    """Handle Google Drive uploads with OAuth authentication."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize Google Drive uploader."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._folder_cache_store = Store(hass, FOLDER_CACHE_VERSION, FOLDER_CACHE_KEY)
        self._credentials: Credentials | None = None
        self._service = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._base_folder_id: str | None = None  # Base "Morning Routine" folder
        self._folder_cache: dict = {}  # Cache of folder IDs: {child: folder_id}
        self._enabled = False

    async def setup(
        self,
        client_id: str,
        client_secret: str,
        folder_id: str | None = None,
    ) -> None:
        """Setup Google Drive uploader with credentials.

        Args:
            client_id: Google OAuth client ID
            client_secret: Google OAuth client secret
            folder_id: Optional Google Drive base folder ID (Morning Routine folder)
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_folder_id = folder_id

        # Load folder cache
        await self._load_folder_cache()

        # Try to load existing credentials
        await self._load_credentials()

        if self._credentials and self._credentials.valid:
            self._enabled = True
            _LOGGER.info("Google Drive uploader initialized successfully")
        elif self._credentials and self._credentials.expired and self._credentials.refresh_token:
            # Try to refresh
            try:
                await self.hass.async_add_executor_job(
                    partial(self._credentials.refresh, Request())
                )
                await self._save_credentials()
                self._enabled = True
                _LOGGER.info("Google Drive credentials refreshed successfully")
            except Exception as ex:
                _LOGGER.error(f"Failed to refresh Google Drive credentials: {ex}")
                self._enabled = False
        else:
            _LOGGER.warning(
                "Google Drive uploader needs authorization. "
                "Use the 'Authorize Google Drive' button in the integration options."
            )
            self._enabled = False

    async def _load_credentials(self) -> None:
        """Load credentials from storage."""
        try:
            data = await self._store.async_load()
            if data and "token" in data:
                self._credentials = Credentials(
                    token=data["token"],
                    refresh_token=data.get("refresh_token"),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    scopes=SCOPES,
                )
                _LOGGER.debug("Loaded Google Drive credentials from storage")
        except Exception as ex:
            _LOGGER.error(f"Failed to load credentials: {ex}")

    async def _save_credentials(self) -> None:
        """Save credentials to storage."""
        try:
            if self._credentials:
                data = {
                    "token": self._credentials.token,
                    "refresh_token": self._credentials.refresh_token,
                }
                await self._store.async_save(data)
                _LOGGER.debug("Saved Google Drive credentials to storage")
        except Exception as ex:
            _LOGGER.error(f"Failed to save credentials: {ex}")

    async def _load_folder_cache(self) -> None:
        """Load folder cache from storage."""
        try:
            data = await self._folder_cache_store.async_load()
            if data:
                # Validate cache format: {child: folder_id}
                # If old format detected {child: {date: folder_id}}, clear it
                is_valid = all(isinstance(v, str) for v in data.values())
                if is_valid:
                    self._folder_cache = data
                    _LOGGER.debug(f"Loaded folder cache: {len(self._folder_cache)} entries")
                else:
                    _LOGGER.info("Old folder cache format detected, clearing cache")
                    self._folder_cache = {}
                    await self._save_folder_cache()
            else:
                self._folder_cache = {}
        except Exception as ex:
            _LOGGER.error(f"Failed to load folder cache: {ex}")
            self._folder_cache = {}

    async def _save_folder_cache(self) -> None:
        """Save folder cache to storage."""
        try:
            await self._folder_cache_store.async_save(self._folder_cache)
            _LOGGER.debug("Saved folder cache to storage")
        except Exception as ex:
            _LOGGER.error(f"Failed to save folder cache: {ex}")

    def get_authorization_url(self, redirect_uri: str) -> str:
        """Get OAuth authorization URL.

        Args:
            redirect_uri: OAuth redirect URI (must match Google Console config)

        Returns:
            Authorization URL for user to visit
        """
        if not self._client_id or not self._client_secret:
            raise ValueError("Client ID and secret not configured")

        client_config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }

        self._flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )

        auth_url, _ = self._flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )

        _LOGGER.info(f"Generated authorization URL with redirect_uri: {redirect_uri}")

        return auth_url

    async def handle_authorization_callback(self, code: str, redirect_uri: str) -> bool:
        """Handle OAuth callback with authorization code.

        Args:
            code: Authorization code from OAuth callback
            redirect_uri: OAuth redirect URI (must match auth request)

        Returns:
            True if authorization successful
        """
        try:
            # Recreate flow if needed (in case of restart)
            if not hasattr(self, "_flow") or self._flow is None:
                if not self._client_id or not self._client_secret:
                    raise ValueError("Client ID and secret not configured")

                client_config = {
                    "installed": {
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": [redirect_uri],
                    }
                }

                self._flow = Flow.from_client_config(
                    client_config,
                    scopes=SCOPES,
                    redirect_uri=redirect_uri,
                )

            # Exchange code for credentials
            await self.hass.async_add_executor_job(
                partial(self._flow.fetch_token, code=code)
            )

            self._credentials = self._flow.credentials
            await self._save_credentials()
            self._enabled = True

            _LOGGER.info("Google Drive authorization successful")
            return True

        except Exception as ex:
            _LOGGER.error(f"Failed to handle authorization callback: {ex}", exc_info=True)
            return False

    async def _get_or_create_folder_structure(
        self, child: str
    ) -> str | None:
        """Get or create folder structure: Morning Routine / [child].

        Args:
            child: Child name (duarte or leonor)

        Returns:
            Folder ID for the child folder, or None if failed
        """
        # Check cache first
        if child in self._folder_cache:
            cached_id = self._folder_cache[child]
            _LOGGER.debug(f"Using cached folder ID for {child}: {cached_id}")
            return cached_id

        try:
            # Ensure credentials are valid
            if self._credentials.expired and self._credentials.refresh_token:
                await self.hass.async_add_executor_job(
                    partial(self._credentials.refresh, Request())
                )
                await self._save_credentials()

            # Build Drive service
            service = await self.hass.async_add_executor_job(
                partial(build, "drive", "v3", credentials=self._credentials)
            )

            # Get or create base folder ("Morning Routine")
            base_folder_id = self._base_folder_id
            if not base_folder_id:
                base_folder_id = await self._find_or_create_folder(
                    service, "Morning Routine", None
                )
                if not base_folder_id:
                    _LOGGER.error("Failed to find or create base 'Morning Routine' folder")
                    return None
                self._base_folder_id = base_folder_id

            # Get or create child folder
            child_folder_id = await self._find_or_create_folder(
                service, child.capitalize(), base_folder_id
            )
            if not child_folder_id:
                _LOGGER.error(f"Failed to create folder for {child}")
                return None

            # Cache the folder ID
            self._folder_cache[child] = child_folder_id
            await self._save_folder_cache()

            _LOGGER.info(f"Folder structure ready: Morning Routine/{child.capitalize()} (ID: {child_folder_id})")
            return child_folder_id

        except Exception as ex:
            _LOGGER.error(f"Failed to create folder structure: {ex}")
            return None

    async def _find_or_create_folder(
        self, service, folder_name: str, parent_id: str | None
    ) -> str | None:
        """Find or create a folder in Google Drive.

        Args:
            service: Google Drive service instance
            folder_name: Name of the folder
            parent_id: Parent folder ID (None for root)

        Returns:
            Folder ID if successful, None otherwise
        """
        try:
            # Search for existing folder
            query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            if parent_id:
                query += f" and '{parent_id}' in parents"

            results = await self.hass.async_add_executor_job(
                lambda: service.files()
                .list(q=query, spaces="drive", fields="files(id, name)")
                .execute()
            )

            files = results.get("files", [])
            if files:
                folder_id = files[0]["id"]
                _LOGGER.debug(f"Found existing folder '{folder_name}': {folder_id}")
                return folder_id

            # Create folder if not found
            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                file_metadata["parents"] = [parent_id]

            folder = await self.hass.async_add_executor_job(
                lambda: service.files()
                .create(body=file_metadata, fields="id,name")
                .execute()
            )

            folder_id = folder.get("id")
            _LOGGER.info(f"Created folder '{folder_name}': {folder_id}")
            return folder_id

        except Exception as ex:
            _LOGGER.error(f"Failed to find/create folder '{folder_name}': {ex}")
            return None

    async def upload_file(
        self,
        filepath: str,
        filename: str | None = None,
        mime_type: str | None = None,
        child: str | None = None,
    ) -> str | None:
        """Upload file to Google Drive with hierarchical folder structure.

        Args:
            filepath: Local file path to upload
            filename: Optional custom filename (defaults to basename of filepath)
            mime_type: Optional MIME type (auto-detected if not provided)
            child: Child name (duarte or leonor) - required for folder structure

        Returns:
            Google Drive file ID if successful, None otherwise
        """
        if not self._enabled:
            _LOGGER.warning("Google Drive uploader not enabled, skipping upload")
            return None

        if not child:
            _LOGGER.error("Child name is required for upload")
            return None

        try:
            # Get or create folder structure (no date folder)
            folder_id = await self._get_or_create_folder_structure(child)
            if not folder_id:
                _LOGGER.error("Failed to prepare folder structure")
                return None

            # Ensure credentials are valid
            if self._credentials.expired and self._credentials.refresh_token:
                await self.hass.async_add_executor_job(
                    partial(self._credentials.refresh, Request())
                )
                await self._save_credentials()

            # Build Drive service
            service = await self.hass.async_add_executor_job(
                partial(build, "drive", "v3", credentials=self._credentials)
            )

            # Generate new filename in format: YYYYMMDD_activity.ext
            original_filename = os.path.basename(filepath)
            new_filename = self._convert_filename_format(original_filename, child)

            file_metadata = {
                "name": new_filename,
                "parents": [folder_id],
            }

            # Auto-detect MIME type if not provided
            if mime_type is None:
                ext = os.path.splitext(filepath)[1].lower()
                mime_types = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webm": "audio/webm",
                    ".mp3": "audio/mpeg",
                    ".wav": "audio/wav",
                }
                mime_type = mime_types.get(ext, "application/octet-stream")

            # Upload file
            media = MediaFileUpload(filepath, mimetype=mime_type, resumable=True)

            file = await self.hass.async_add_executor_job(
                lambda: service.files()
                .create(body=file_metadata, media_body=media, fields="id,name,webViewLink")
                .execute()
            )

            file_id = file.get("id")
            web_link = file.get("webViewLink")

            _LOGGER.info(
                f"Successfully uploaded {new_filename} to Google Drive (ID: {file_id})"
            )
            _LOGGER.debug(f"Google Drive link: {web_link}")

            return file_id

        except HttpError as ex:
            _LOGGER.error(f"Google Drive API error: {ex}")
            return None
        except Exception as ex:
            _LOGGER.error(f"Failed to upload file to Google Drive: {ex}")
            return None

    async def create_folder(self, folder_name: str) -> str | None:
        """Create a folder in Google Drive.

        Args:
            folder_name: Name of folder to create

        Returns:
            Folder ID if successful, None otherwise
        """
        if not self._enabled:
            _LOGGER.warning("Google Drive uploader not enabled")
            return None

        try:
            # Ensure credentials are valid
            if self._credentials.expired and self._credentials.refresh_token:
                await self.hass.async_add_executor_job(
                    partial(self._credentials.refresh, Request())
                )
                await self._save_credentials()

            # Build Drive service
            service = await self.hass.async_add_executor_job(
                partial(build, "drive", "v3", credentials=self._credentials)
            )

            # Create folder
            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }

            folder = await self.hass.async_add_executor_job(
                lambda: service.files()
                .create(body=file_metadata, fields="id,name")
                .execute()
            )

            folder_id = folder.get("id")
            _LOGGER.info(f"Created folder '{folder_name}' in Google Drive (ID: {folder_id})")

            return folder_id

        except Exception as ex:
            _LOGGER.error(f"Failed to create folder in Google Drive: {ex}")
            return None

    def _convert_filename_format(self, filename: str, child: str) -> str:
        """Convert filename from local format to Drive format.

        Args:
            filename: Original filename (e.g., duarte_20260119_083640.jpg or duarte_breakfast_20260119_083640.webm)
            child: Child name (duarte or leonor)

        Returns:
            New filename in format YYYYMMDD_activity.ext (e.g., 20260119_dressed.jpg or 20260119_breakfast.webm)
        """
        try:
            # Remove child name prefix
            without_child = filename.replace(f"{child}_", "", 1)

            # Get file extension
            ext = os.path.splitext(filename)[1]

            # Determine activity
            if "breakfast" in without_child:
                activity = "breakfast"
                # Extract date from: breakfast_YYYYMMDD_HHMMSS.ext
                parts = without_child.replace("breakfast_", "")
                date_part = parts.split("_")[0]  # YYYYMMDD
            else:
                activity = "dressed"
                # Extract date from: YYYYMMDD_HHMMSS.ext
                date_part = without_child.split("_")[0]  # YYYYMMDD

            # Format: YYYYMMDD_activity.ext
            new_filename = f"{date_part}_{activity}{ext}"
            _LOGGER.debug(f"Converted filename: {filename} -> {new_filename}")

            return new_filename

        except Exception as ex:
            _LOGGER.warning(f"Failed to convert filename format, using original: {ex}")
            return filename

    @property
    def is_enabled(self) -> bool:
        """Return if uploader is enabled and ready."""
        return self._enabled

    @property
    def is_authorized(self) -> bool:
        """Return if uploader has valid credentials."""
        return self._credentials is not None and self._credentials.valid
