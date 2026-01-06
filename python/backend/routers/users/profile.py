# Consolidated imports  
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    HTTPException, BaseModel, text, db
)
from datetime import datetime

from backend.repositories.users import UserRepository
from backend.db.models import User

router = create_router("/user", "User Profile")

class UserNameRequest(BaseModel):
    name: str

class UserContactRequest(BaseModel):
    name: str
    phone_number: str

class UserContactNameRequest(BaseModel):
    name: str

@router.get("/primary_identifier")
@safe_endpoint
def get_user_primary_identifier():
    log_simple("Getting user primary identifier")
    
    with db.session_scope() as session:
        identifiers = UserRepository.get_user_primary_identifier(session)
        
        if identifiers:
            return identifiers
        else:
            return {"message": "No primary identifier found"}

@router.post("/me/name")
@safe_endpoint
async def update_user_name(request: UserNameRequest):
    log_simple(f"Updating user name: {request.name}")
    
    with db.session_scope() as session:
        result = UserRepository.create_or_update_user_contact(session, request.name)
        session.commit()
        
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

@router.post("/contact")
@safe_endpoint
async def create_update_user_contact(request: UserContactRequest):
    log_simple(f"Updating user contact name: {request.name} (phone ignored - deprecated)")
    
    with db.session_scope() as session:
        # This function now only updates the name, ignoring the phone number
        result = UserRepository.create_or_update_user_contact(session, request.name)
        session.commit()
        
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

@router.post("/user_contact_name")
@safe_endpoint
async def update_user_contact_name(request: UserContactNameRequest):
    log_simple(f"Updating user contact name: {request.name}")

    with db.session_scope() as session:
        try:
            result = UserRepository.update_user_contact_name(session, request.name)
            session.commit()
            log_simple("User contact name updated successfully")
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=404, detail=str(e))

@router.get("/contact_id")
@safe_endpoint
def get_user_contact_id():
    log_simple("Getting user contact ID")
    
    with db.session_scope() as session:
        contact_id = UserRepository.get_user_contact_id(session)
        
        if contact_id:
            return {"contact_id": contact_id}
        else:
            raise HTTPException(status_code=404, detail="User contact not found")