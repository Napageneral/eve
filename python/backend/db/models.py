# NOTE: Core chat-data tables are defined in db/etl_models.py
# NOTE: Context/Report related models are defined in db/context_models.py
# This file imports them all and re-exports for convenience.

from datetime import datetime, timezone
import json
from typing import Any, Dict

from sqlalchemy import Column, Index, Integer, String, DateTime, Boolean, ForeignKey, Text, LargeBinary, inspect, create_engine, Float, UniqueConstraint, Date
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.types import JSON
from sqlalchemy.dialects.postgresql import UUID
import uuid

# Import the shared Base from base.py
from .base import Base

# Import models from other files
from .etl_models import (
    Attachment,
    Chat,
    ChatParticipant,
    Contact,
    ContactIdentifier,
    Conversation,
    Message,
    Reaction
)
from .context_models import (
    # ContextDefinition and ContextSelection removed - Eve system handles this now
    PromptTemplate,
    Report,
    ReportDisplay,
    PublishedReport
)

# Define a helper function to get current UTC time
def current_time_utc():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=True, index=True)
    email = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=current_time_utc, onupdate=current_time_utc, nullable=False)

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"

class ChatSubscription(Base):
    __tablename__ = "chat_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    status = Column(String, nullable=False, default="active", index=True)
    provider = Column(String, nullable=True)
    provider_subscription_id = Column(String, nullable=True, unique=True, index=True)
    
    current_period_start = Column(DateTime(timezone=True), default=current_time_utc, nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=current_time_utc, onupdate=current_time_utc, nullable=False)

    chat = relationship("Chat", backref="subscription") 
    user = relationship("User", backref="chat_subscriptions")

    def __repr__(self):
        return f"<ChatSubscription(id={self.id}, chat_id={self.chat_id}, user_id={self.user_id}, status='{self.status}', ends_at='{self.current_period_end}')>"

class Commitment(Base):
    __tablename__ = "commitments"
    
    id = Column(Integer, primary_key=True)
    commitment_id = Column(String, unique=True, index=True)  # e.g., "commit_20240315_001"
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)  # Who made the commitment
    to_person_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)  # To whom
    
    commitment_text = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    
    created_date = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    due_date = Column(Date, nullable=True)
    due_specificity = Column(String, nullable=False)  # explicit|inferred|vague
    
    status = Column(String, nullable=False, default="pending")  # pending|monitoring_condition|completed|cancelled|failed
    priority = Column(String, nullable=True)  # high|medium|low
    
    # For conditional commitments
    condition = Column(Text, nullable=True)
    
    # Modification tracking
    modifications = Column(JSON, nullable=True, default=lambda: [])
    
    # Reminder tracking
    reminder_data = Column(JSON, nullable=True)
    
    # Completion/cancellation metadata
    completed_date = Column(DateTime(timezone=True), nullable=True)
    resolution_method = Column(String, nullable=True)  # detected|user_confirmed|user_reported
    final_due_date = Column(Date, nullable=True)
    
    # New columns for snapshot management and generation tracking
    analysis_timestamp = Column(DateTime(timezone=True), nullable=True)
    message_timestamp = Column(DateTime(timezone=True), nullable=True)
    analysis_generation_id = Column(String(255), nullable=True)
    
    # Conversation-level tracking for conversation-aware snapshots
    source_conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)
    last_modified_conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=current_time_utc, onupdate=current_time_utc, nullable=False)
    
    # Relationships
    conversation = relationship("Conversation", foreign_keys=[conversation_id], backref="commitments")
    chat = relationship("Chat", backref="commitments")
    committer = relationship("Contact", foreign_keys=[contact_id], backref="commitments_made")
    recipient = relationship("Contact", foreign_keys=[to_person_id], backref="commitments_received")
    
    # Additional relationships for conversation tracking
    source_conversation = relationship("Conversation", foreign_keys=[source_conversation_id])
    last_modified_conversation = relationship("Conversation", foreign_keys=[last_modified_conversation_id])

    def __repr__(self):
        return f"<Commitment(id={self.id}, commitment_id='{self.commitment_id}', status='{self.status}', due_date='{self.due_date}')>"

class CommitmentEventLog(Base):
    __tablename__ = "commitment_event_log"
    
    id = Column(Integer, primary_key=True)
    event_id = Column(String(255), unique=True, nullable=False, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # 'snapshot', 'update', 'delete', etc.
    event_data = Column(JSON, nullable=False)
    generation_id = Column(String(255), nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    applied = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    
    # Relationships
    chat = relationship("Chat", backref="commitment_events")

    def __repr__(self):
        return f"<CommitmentEventLog(id={self.id}, event_id='{self.event_id}', chat_id={self.chat_id}, event_type='{self.event_type}')>"

class CommitmentAnalysisGeneration(Base):
    __tablename__ = "commitment_analysis_generations"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    generation_id = Column(String(255), nullable=False, unique=True, index=True)
    message_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    analysis_timestamp = Column(DateTime(timezone=True), nullable=False)
    applied = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    
    # Relationships
    chat = relationship("Chat", backref="commitment_generations")

    def __repr__(self):
        return f"<CommitmentAnalysisGeneration(id={self.id}, generation_id='{self.generation_id}', chat_id={self.chat_id}, applied={self.applied})>"

class ConversationAnalysis(Base):
    __tablename__ = "conversation_analyses"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=True, index=True)  # Nullable for Eve prompts
    eve_prompt_id = Column(String, nullable=True, index=True)  # Eve prompt ID (e.g., "convo-all-v1")
    
    status = Column(String, nullable=False, default="pending", index=True)
    
    temporal_workflow_id = Column(String, nullable=True, index=True)

    completion_id = Column(Integer, ForeignKey("completions.id"), nullable=True) 

    error_message = Column(Text, nullable=True) 
    retry_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=current_time_utc, onupdate=current_time_utc, nullable=False)

    conversation = relationship("Conversation", backref="analyses")
    prompt_template = relationship("PromptTemplate", backref="conversation_analyses_instances")
    completion_record = relationship("Completion", backref="conversation_analysis_link")

    __table_args__ = (
        UniqueConstraint("conversation_id", "prompt_template_id", name="uq_conversation_prompt_template"),
    )

    def __repr__(self):
        return f"<ConversationAnalysis(id={self.id}, convo_id={self.conversation_id}, prompt_id={self.prompt_template_id}, status='{self.status}')>"

class Completion(Base):
    __tablename__ = 'completions'
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), nullable=True, index=True)
    prompt_template_id = Column(Integer, ForeignKey('prompt_templates.id'), nullable=False, index=True)
    compiled_prompt_text = Column(Text, nullable=True)
    model = Column(String)
    result = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=current_time_utc)
    
    conversation = relationship("Conversation", backref="completions")
    chat = relationship("Chat", backref="completions")
    contact = relationship("Contact", backref="completions")
    prompt_template = relationship("PromptTemplate", backref="completions")

class Entity(Base):
    __tablename__ = 'entities'
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), index=True)
    title = Column(String)
    created_at = Column(DateTime(timezone=True), default=current_time_utc)
    
    conversation = relationship("Conversation", backref="entities")
    chat = relationship("Chat", backref="entities")
    contact = relationship("Contact", backref="entities")
    __table_args__ = (
        Index('idx_entities_conv_title', 'conversation_id', 'title'),
    )

class Topic(Base):
    __tablename__ = 'topics'
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), index=True)
    title = Column(String)
    created_at = Column(DateTime(timezone=True), default=current_time_utc)
    
    conversation = relationship("Conversation", backref="topics")
    chat = relationship("Chat", backref="topics")
    contact = relationship("Contact", backref="topics")
    __table_args__ = (
        Index('idx_topics_conv_title', 'conversation_id', 'title'),
    )

class Emotion(Base):
    __tablename__ = 'emotions'
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), nullable=True, index=True)
    emotion_type = Column(String)
    created_at = Column(DateTime(timezone=True), default=current_time_utc)
    
    conversation = relationship("Conversation", backref="emotions")
    chat = relationship("Chat", backref="emotions")
    contact = relationship("Contact", backref="emotions")
    __table_args__ = (
        Index('idx_emotions_conv_type', 'conversation_id', 'emotion_type'),
    )

class HumorItem(Base):
    __tablename__ = 'humor_items'
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), index=True)
    snippet = Column(Text)
    created_at = Column(DateTime(timezone=True), default=current_time_utc)

    conversation = relationship("Conversation", backref="humor_items")
    chat = relationship("Chat", backref="humor_items")
    contact = relationship("Contact", backref="humor_items")

# DLQ Model for storing failed tasks
class FailedTask(Base):
    __tablename__ = 'failed_tasks'
    
    id = Column(Integer, primary_key=True)
    task_id = Column(String(255), unique=True, nullable=False)
    task_name = Column(String(255), nullable=False)
    queue_name = Column(String(100), nullable=False)
    args = Column(Text)  # JSON serialized args
    kwargs = Column(Text)  # JSON serialized kwargs
    error_message = Column(Text)
    failed_at = Column(DateTime, default=current_time_utc)
    retry_count = Column(Integer, default=0)
    last_retry_at = Column(DateTime)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime)
    
    def __repr__(self):
        return f"<FailedTask(id={self.id}, task_name='{self.task_name}', failed_at='{self.failed_at}')>"

class AppSettings(Base):
    __tablename__ = 'app_settings'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)  # Store as text, can be JSON for complex values
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=current_time_utc, onupdate=current_time_utc, nullable=False)
    
    def __repr__(self):
        return f"<AppSettings(key='{self.key}', value='{self.value}')>"

# AI Chatbot Models (converted from ai-chatbot Drizzle schema)
class ChatbotUser(Base):
    __tablename__ = "chatbot_users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    email = Column(String(64), nullable=False, index=True)
    password = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=current_time_utc, onupdate=current_time_utc, nullable=False)
    
    def __repr__(self):
        return f"<ChatbotUser(id={self.id}, email='{self.email}')>"

class ChatbotThread(Base):
    __tablename__ = "chatbot_chats"  # Keep existing table name for backward compatibility
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    title = Column(Text, nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_users.id"), nullable=False, index=True)
    visibility = Column(String, nullable=False, default="private")  # 'public' or 'private'
    
    user = relationship("ChatbotUser", backref="chats")
    
    def __repr__(self):
        return f"<ChatbotThread(id={self.id}, title='{self.title}', visibility='{self.visibility}')>"

class ChatbotMessage(Base):
    __tablename__ = "chatbot_messages_v2"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    chat_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_chats.id"), nullable=False, index=True)
    role = Column(String, nullable=False)  # 'user', 'assistant', 'system'
    parts = Column(JSON, nullable=False)  # Message parts (text, images, etc.)
    attachments = Column(JSON, nullable=False)  # File attachments
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    
    chat = relationship("ChatbotThread", backref="messages")
    
    def __repr__(self):
        return f"<ChatbotMessage(id={self.id}, chat_id={self.chat_id}, role='{self.role}')>"

class ChatbotVote(Base):
    __tablename__ = "chatbot_votes_v2"
    
    chat_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_chats.id"), primary_key=True, nullable=False)
    message_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_messages_v2.id"), primary_key=True, nullable=False)
    is_upvoted = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    
    chat = relationship("ChatbotThread", backref="votes")
    message = relationship("ChatbotMessage", backref="votes")
    
    def __repr__(self):
        return f"<ChatbotVote(chat_id={self.chat_id}, message_id={self.message_id}, is_upvoted={self.is_upvoted})>"

class ChatbotDocument(Base):
    __tablename__ = "chatbot_documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    created_at = Column(DateTime(timezone=True), primary_key=True, default=current_time_utc, nullable=False)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=True)
    kind = Column(String, nullable=False, default="text")  # 'text', 'code', 'image', 'sheet'
    user_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_users.id"), nullable=False, index=True)
    # Optional origin chatbot tab id to disambiguate concurrent streams
    origin_chat_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_chats.id"), nullable=True, index=True)
    
    user = relationship("ChatbotUser", backref="documents")
    origin_chat = relationship("ChatbotThread")
    
    def __repr__(self):
        return f"<ChatbotDocument(id={self.id}, title='{self.title}', kind='{self.kind}', origin_chat_id={self.origin_chat_id})>"

class ChatbotSuggestion(Base):
    __tablename__ = "chatbot_suggestions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    document_id = Column(UUID(as_uuid=True), nullable=False)
    document_created_at = Column(DateTime(timezone=True), nullable=False)
    original_text = Column(Text, nullable=False)
    suggested_text = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    is_resolved = Column(Boolean, nullable=False, default=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    
    user = relationship("ChatbotUser", backref="suggestions")
    
    # Note: Foreign key constraint to ChatbotDocument will be handled in migration
    # due to composite primary key on ChatbotDocument
    
    def __repr__(self):
        return f"<ChatbotSuggestion(id={self.id}, document_id={self.document_id}, is_resolved={self.is_resolved})>"

class ChatbotStream(Base):
    __tablename__ = "chatbot_streams"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    chat_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_chats.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=current_time_utc, nullable=False)
    
    chat = relationship("ChatbotThread", backref="streams")
    
    def __repr__(self):
        return f"<ChatbotStream(id={self.id}, chat_id={self.chat_id})>"

# Central export list
__all__ = [
    # From models.py (this file)
    "Base",
    "Completion",
    "Entity",
    "Topic",
    "Emotion",
    "HumorItem",
    "User",
    "ChatSubscription",
    "ConversationAnalysis",
    "Commitment",
    "CommitmentEventLog",
    "CommitmentAnalysisGeneration",

    # AI Chatbot models
    "ChatbotUser",
    "ChatbotThread", 
    "ChatbotMessage",
    "ChatbotVote",
    "ChatbotDocument",
    "ChatbotSuggestion",
    "ChatbotStream",

    # From etl_models.py
    "Attachment",
    "Chat",
    "ChatParticipant",
    "Contact",
    "ContactIdentifier",
    "Conversation",
    "Message",
    "Reaction",

    # From context_models.py
    # "ContextDefinition",  # Removed - Eve system
    # "ContextSelection",   # Removed - Eve system
    "PromptTemplate",
    "Report",
    "ReportDisplay",
    "PublishedReport",

    # From DLQ
    "FailedTask",
    "AppSettings"
]