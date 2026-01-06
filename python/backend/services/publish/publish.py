# File: app/backend/services/publish_service.py
from sqlalchemy.orm import Session
from backend.db.context_models import Report, PublishedReport
from backend.db.models import Chat, Contact
from backend.repositories.published_reports import PublishedReportRepository
from backend.repositories.reports import ReportRepository
from backend.services.core.utils import dict_to_obj
from sqlalchemy.sql import text
import secrets
import hashlib
import logging

logger = logging.getLogger(__name__)

def _generate_preview_description(session: Session, report: Report) -> str:
    """Generate a description for the report preview based on context and title."""
    return PublishedReportRepository.generate_preview_description(
        session, report.id, report.chat_id, report.contact_id, report.title
    )

def publish_report(
    session: Session,
    report_id: int,
    report_display_id: int = None,
    preview_image_url: str = None,
    preview_description: str = None,
    is_password_protected: bool = False,
    password: str = None,
    password_hint: str = None
) -> int:

    report = ReportRepository.get_report(session, report_id)
    if not report:
        raise ValueError(f"Report ID={report_id} not found")
    
    # If no preview description was provided, generate one
    final_description = preview_description
    if not final_description or not final_description.strip():
        final_description = _generate_preview_description(session, report)
        logger.info(f"Auto-generated preview description for report {report_id}")

    # Use repository to create published report
    report_data = {
        "report_id": report_id,
        "report_display_id": report_display_id,
        "preview_image_url": preview_image_url,
        "preview_description": final_description,
        "is_password_protected": is_password_protected,
        "password": password,
        "password_hint": password_hint
    }
    
    return PublishedReportRepository.create_published_report(session, report_data)

def get_published_report(session: Session, slug: str):
    """Get published report by slug using repository."""
    result = PublishedReportRepository.get_published_report(session, slug)
    
    if result:
        return dict_to_obj(result)
    return None

def verify_password(session: Session, published_id: int, password: str) -> bool:
    """Verify password using repository."""
    return PublishedReportRepository.verify_password(session, published_id, password)