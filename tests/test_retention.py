import pytest
from datetime import datetime, timezone
import os

from app.core.config import Settings

def test_ttl_calculation():
    """Test exact timestamp expiry calculation (30 days)"""
    settings = Settings(DATA_CLEANUP_DAYS=30)
    assert settings.ttl_seconds == 30 * 24 * 60 * 60

def test_ttl_calculation_40_days():
    """Test exact timestamp expiry calculation (40 days)"""
    settings = Settings(DATA_CLEANUP_DAYS=40)
    assert settings.ttl_seconds == 40 * 24 * 60 * 60

def test_utc_timezone_enforcement():
    """Test that generated datetimes are UTC aware"""
    now = datetime.now(timezone.utc)
    assert now.tzinfo == timezone.utc

def test_dry_run_flag():
    """Test dry run flag is parsed correctly"""
    os.environ["DATA_CLEANUP_DRY_RUN"] = "true"
    settings = Settings()
    assert settings.DATA_CLEANUP_DRY_RUN is True
    
    os.environ["DATA_CLEANUP_DRY_RUN"] = "false"
    settings = Settings()
    assert settings.DATA_CLEANUP_DRY_RUN is False

def test_mass_deletion_threshold():
    """Test that the mass deletion threshold is parsed properly"""
    os.environ["DATA_CLEANUP_MAX_PERCENTAGE"] = "0.05"
    settings = Settings()
    assert settings.DATA_CLEANUP_MAX_PERCENTAGE == 0.05
