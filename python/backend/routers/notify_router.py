# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple, 
    HTTPException, BaseModel, text, db
)
from twilio.rest import Client

router = create_router("/notify", "Notifications")

# Twilio configuration
ACCOUNT_SID = "ACd42562beb509e81bc6c6d33fa7fa5a52"
AUTH_TOKEN = "0024f2fe8ccb099d12264229e623ee9f"

class NotifyRequest(BaseModel):
    pass

@router.post("/sms")
@safe_endpoint
async def notify_sms(req: NotifyRequest):
    log_simple("Sending SMS notification")
    
    # Get user's primary phone number
    with db.session_scope() as session:
        row = session.execute(text("""
            SELECT ci.identifier
            FROM contacts c
            JOIN contact_identifiers ci ON c.id = ci.contact_id
            WHERE c.is_me = 1
              AND ci.type = 'Phone'
              AND ci.is_primary = 1
            LIMIT 1
        """)).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No primary phone number found")

    to_number = row[0]
    log_simple(f"Sending SMS to {to_number}")

    # Send message via Twilio
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    message = client.messages.create(
        messaging_service_sid="MG4dfe76c807675f0767761315972a4eed",
        body="Your Humor Analysis is done!",
        to=to_number
    )
    
    log_simple(f"SMS sent: {message.sid}")
    return {"success": True, "message_sid": message.sid}
