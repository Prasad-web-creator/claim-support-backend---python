"""
Scanner Service — migrated from ScannerService.js.
Abstraction layer for virus scanning. Mock implementation for now.
"""


class ScannerService:
    """Virus scanning abstraction. Ready for ClamAV integration."""

    @staticmethod
    async def scan_uploaded_file(file_path: str) -> bool:
        """
        Scan a file for malware.
        Returns True if clean, raises Exception if infected.
        Current implementation: mock success.
        """
        return True
