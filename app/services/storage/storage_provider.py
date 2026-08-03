"""
Abstract Base Class for Storage Providers.
"""

from abc import ABC, abstractmethod


class StorageProvider(ABC):
    
    @abstractmethod
    async def upload_file(self, buffer: bytes, storage_key: str, metadata: dict | None = None) -> dict:
        """Upload a file to storage."""
        pass

    @abstractmethod
    async def download_file(self, storage_key: str):
        """Download a file (returns an async generator/stream)."""
        pass

    @abstractmethod
    async def delete_file(self, storage_key: str) -> bool:
        """Delete a file from storage."""
        pass

    @abstractmethod
    async def generate_signed_url(self, storage_key: str, expires_in_seconds: int = 900) -> str:
        """Generate a temporary signed URL."""
        pass
