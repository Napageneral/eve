"""App Settings Repository - All database operations related to application settings."""

import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from .core.generic import GenericRepository

logger = logging.getLogger(__name__)


class AppSettingsRepository(GenericRepository):
    """Repository for all app settings database operations"""
    
    TABLE = "app_settings"
    
    @classmethod
    def get_app_setting(cls, session: Session, key: str) -> Optional[str]:
        """Get app setting value by key."""
        return cls.fetch_scalar(session, "SELECT value FROM app_settings WHERE key = :key", {"key": key})
    
    @classmethod
    def set_app_setting(cls, session: Session, key: str, value: str) -> Dict[str, Any]:
        """Set app setting value."""
        current_time = datetime.utcnow()

        cls.upsert(
            session,
            "app_settings",
            {"key": key},
            {"value": value, "created_at": current_time, "updated_at": current_time},
        )
        
        return {"success": True, "key": key, "value": value}
    
    @classmethod
    def get_onboarding_status(cls, session: Session) -> bool:
        """Get onboarding completion status."""
        value = cls.get_app_setting(session, "onboardingCompleted")
        return value == "true" if value else False
    
    @classmethod
    def mark_onboarding_complete(cls, session: Session) -> Dict[str, Any]:
        """Mark onboarding as complete."""
        cls.set_app_setting(session, "onboardingCompleted", "true")
        return {"success": True, "completed": True}
    
    @classmethod
    def get_all_settings(cls, session: Session) -> Dict[str, str]:
        """Get all app settings as a dictionary."""
        settings = cls.fetch_all(session, "SELECT key, value FROM app_settings")
        return {setting["key"]: setting["value"] for setting in settings} 