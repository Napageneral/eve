from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from .core.generic import GenericRepository


class ChatSubscriptionRepository(GenericRepository):
    """Repository for chat subscription operations."""
    
    TABLE = "chat_subscriptions"
    
    @classmethod
    def has_active(cls, session: Session, user_id: int, chat_id: int) -> bool:
        """Check if the user has an active subscription for the chat."""
        sql = """
        SELECT 1 FROM chat_subscriptions
        WHERE user_id = :user_id 
          AND chat_id = :chat_id
          AND status = 'active'
          AND current_period_end >= :current_time
        LIMIT 1
        """
        params = {"user_id": user_id, "chat_id": chat_id, "current_time": datetime.utcnow()}
        return cls.fetch_scalar(session, sql, params) is not None

    @classmethod
    def activate_dev_subscription(
        cls, 
        session: Session, 
        user_id: int, 
        chat_id: int, 
        period_days: int = 365
    ) -> int:
        """Activate or create a subscription for development/testing purposes."""
        now = datetime.utcnow()
        period_end = now + timedelta(days=period_days)

        return cls.upsert(
            session,
            "chat_subscriptions",
            {"user_id": user_id, "chat_id": chat_id},
            {
                "status": "active",
                "current_period_start": now,
                "current_period_end": period_end,
                "provider": "dev_activation",
                "updated_at": now,
            },
        ) 