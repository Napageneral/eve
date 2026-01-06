from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone

from backend.db.session_manager import get_db

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/subscription-status")
async def get_subscription_status(session: Session = Depends(get_db)):
    """Return current subscription status for the user.

    NOTE: Single-user Electron app - user_id is always 1.
    Active if any row in chat_subscriptions for user with active status and future period end.
    """
    user_id = 1  # Single-user app mode
    now = datetime.now(timezone.utc)
    try:
      sql = text(
          """
          SELECT status, current_period_end
          FROM chat_subscriptions
          WHERE user_id = :uid
            AND status = 'active'
            AND (current_period_end IS NULL OR current_period_end >= :now)
          ORDER BY current_period_end DESC NULLS LAST
          LIMIT 1
          """
      )
      row = session.execute(sql, {"uid": user_id, "now": now}).fetchone()
      if row:
          status = row[0] or "active"
          cpe = row[1].isoformat() if row[1] else None
          return {"status": status, "isActive": True, "current_period_end": cpe}
      return {"status": "none", "isActive": False}
    except Exception as e:
      # Fail closed (treat as not active) with diagnostic message
      return {"status": "error", "isActive": False, "error": str(e)}


@router.post("/checkout-session")
async def create_subscription_checkout_session(payload: dict, session: Session = Depends(get_db)):
    """Create a subscription checkout session (placeholder).
    Frontend should open the returned URL in an external browser.
    """
    # TODO: Integrate with Stripe or Paddle and return real URL
    plan_id = str(payload.get("planId") or payload.get("plan_id") or "plan_pro_monthly")
    return {"checkout_url": f"https://billing.stripe.com/p/login/test_{plan_id}"}


@router.get("/customer-portal")
async def open_customer_portal(session: Session = Depends(get_db)):
    """Return a URL to the subscription management portal (placeholder)."""
    # TODO: Integrate with Stripe billing portal
    return {"portal_url": "https://billing.stripe.com/p/login/test_portal"}


