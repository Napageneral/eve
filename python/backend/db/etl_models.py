from datetime import datetime

from sqlalchemy import Column, Index, Integer, String, DateTime, Boolean, ForeignKey, Text, LargeBinary
from sqlalchemy.orm import relationship

from .base import Base

class Contact(Base):
    __tablename__ = 'contacts'
    
    id = Column(Integer, primary_key=True)
    name = Column(String)
    nickname = Column(String)
    avatar = Column(LargeBinary)
    last_updated = Column(DateTime)
    data_source = Column(String)
    is_me = Column(Boolean, default=False)

    identifiers = relationship("ContactIdentifier", back_populates="contact")
    messages = relationship("Message", back_populates="sender")
    participations = relationship("ChatParticipant", back_populates="contact")
    
class ContactIdentifier(Base):
    __tablename__ = 'contact_identifiers'
    
    id = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'))
    identifier = Column(String, index=True)
    type = Column(String)  # 'email' or 'phone'
    is_primary = Column(Boolean)
    last_used = Column(DateTime)

    contact = relationship("Contact", back_populates="identifiers")

class Chat(Base):
    __tablename__ = 'chats'
    
    id = Column(Integer, primary_key=True)
    chat_identifier = Column(String, unique=True, index=True)
    chat_name = Column(String)
    created_date = Column(DateTime, index=True)
    last_message_date = Column(DateTime, index=True)
    is_group = Column(Boolean)
    service_name = Column(String)
    is_blocked = Column(Boolean, default=False)
    total_messages = Column(Integer, default=0, nullable=False)
    last_embedding_update = Column(DateTime, nullable=True)
    wrapped_in_progress = Column(Boolean, default=False)
    wrapped_done = Column(Boolean, default=False)

    messages = relationship("Message", back_populates="chat")
    conversations = relationship("Conversation", back_populates="chat")
    participants = relationship("ChatParticipant", back_populates="chat")

class ChatParticipant(Base):
    __tablename__ = 'chat_participants'
    
    chat_id = Column(Integer, ForeignKey('chats.id'), primary_key=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), primary_key=True)

    chat = relationship("Chat", back_populates="participants")
    contact = relationship("Contact", back_populates="participations")

class Conversation(Base):
    __tablename__ = 'conversations'

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    initiator_id = Column(Integer, ForeignKey('contacts.id'), index=True)
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))    
    message_count = Column(Integer)
    summary = Column(Text)
    gap_threshold = Column(Integer, nullable=True)
    
    chat = relationship("Chat", back_populates="conversations")
    initiator = relationship("Contact", foreign_keys=[initiator_id])
    messages = relationship('Message', back_populates='conversation', order_by='Message.timestamp', cascade="all, delete-orphan")

    participants = relationship(
        "Contact",
        secondary="messages",
        primaryjoin="Conversation.id == Message.conversation_id",
        secondaryjoin="Message.sender_id == Contact.id",
        viewonly=True,
        backref="conversations",
    )

    __table_args__ = (
        Index('idx_conversations_chat_start', 'chat_id', 'start_time'),
    )

    def __repr__(self):
        return (f"<Conversation(id={self.id}, chat_id={self.chat_id}, "
                f"start_time={self.start_time}, end_time={self.end_time}, "
                f"message_count={self.message_count})>")

class Message(Base):
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    sender_id = Column(Integer, ForeignKey('contacts.id'), index=True)
    content = Column(Text)
    timestamp = Column(DateTime(timezone=True), index=True)
    is_from_me = Column(Boolean)
    message_type = Column(Integer)
    service_name = Column(String)
    guid = Column(String, unique=True, index=True)
    associated_message_guid = Column(String)
    reply_to_guid = Column(String)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), index=True, nullable=True)

    chat = relationship("Chat", back_populates="messages")
    sender = relationship("Contact", back_populates="messages")
    reactions = relationship("Reaction", back_populates="original_message", foreign_keys="Reaction.original_message_guid")
    attachments = relationship("Attachment", back_populates="message")
    conversation = relationship('Conversation', back_populates='messages')

    __table_args__ = (
        Index('idx_chat_timestamp', chat_id, timestamp),
    )

class Reaction(Base):
    __tablename__ = 'reactions'

    id = Column(Integer, primary_key=True)
    original_message_guid = Column(String, ForeignKey('messages.guid'), index=True)
    timestamp = Column(DateTime(timezone=True), index=True)
    sender_id = Column(Integer, ForeignKey('contacts.id'), index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    reaction_type = Column(Integer)
    guid = Column(String, unique=True, index=True)

    original_message = relationship("Message", back_populates="reactions", foreign_keys=[original_message_guid])
    sender = relationship("Contact", backref="reactions")

class Attachment(Base):
    __tablename__ = 'attachments'
    
    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey('messages.id'), index=True)
    file_name = Column(String)
    mime_type = Column(String)
    size = Column(Integer)
    created_date = Column(DateTime, index=True)
    is_sticker = Column(Boolean)
    guid = Column(String, unique=True, index=True)
    uti = Column(String)

    message = relationship("Message", back_populates="attachments")

class LiveSyncState(Base):
    __tablename__ = "live_sync_state"
    key = Column(String, primary_key=True)  # TEXT PK
    value = Column(String)                  # TEXT – store watermark or any future kv
 