-- Core warehouse schema for ETL data
-- Conversations, messages, chats, contacts, and analysis results

-- Contacts table
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    nickname TEXT,
    avatar BLOB,
    last_updated TIMESTAMP,
    data_source TEXT,
    is_me BOOLEAN DEFAULT 0
);

-- Contact identifiers (phone/email)
CREATE TABLE IF NOT EXISTS contact_identifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL,
    identifier TEXT NOT NULL,
    type TEXT NOT NULL, -- 'email' or 'phone'
    is_primary BOOLEAN DEFAULT 0,
    last_used TIMESTAMP,
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);

CREATE INDEX IF NOT EXISTS idx_contact_identifiers_contact ON contact_identifiers(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_identifiers_identifier ON contact_identifiers(identifier);

-- Chats table
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_identifier TEXT UNIQUE NOT NULL,
    chat_name TEXT,
    created_date TIMESTAMP,
    last_message_date TIMESTAMP,
    is_group BOOLEAN DEFAULT 0,
    service_name TEXT,
    is_blocked BOOLEAN DEFAULT 0,
    total_messages INTEGER DEFAULT 0 NOT NULL,
    last_embedding_update TIMESTAMP,
    wrapped_in_progress BOOLEAN DEFAULT 0,
    wrapped_done BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_chats_identifier ON chats(chat_identifier);
CREATE INDEX IF NOT EXISTS idx_chats_created_date ON chats(created_date);
CREATE INDEX IF NOT EXISTS idx_chats_last_message_date ON chats(last_message_date);

-- Chat participants (many-to-many)
CREATE TABLE IF NOT EXISTS chat_participants (
    chat_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, contact_id),
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);

-- Conversations (grouped messages)
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    initiator_id INTEGER,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    gap_threshold INTEGER,
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (initiator_id) REFERENCES contacts(id)
);

CREATE INDEX IF NOT EXISTS idx_conversations_chat_id ON conversations(chat_id);
CREATE INDEX IF NOT EXISTS idx_conversations_chat_start ON conversations(chat_id, start_time);
CREATE INDEX IF NOT EXISTS idx_conversations_initiator ON conversations(initiator_id);

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    sender_id INTEGER,
    content TEXT,
    timestamp TIMESTAMP NOT NULL,
    is_from_me BOOLEAN DEFAULT 0,
    message_type INTEGER,
    service_name TEXT,
    guid TEXT UNIQUE NOT NULL,
    associated_message_guid TEXT,
    reply_to_guid TEXT,
    conversation_id INTEGER,
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (sender_id) REFERENCES contacts(id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_sender_id ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_chat_timestamp ON messages(chat_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_guid ON messages(guid);

-- Reactions table
CREATE TABLE IF NOT EXISTS reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_message_guid TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    sender_id INTEGER,
    chat_id INTEGER,
    reaction_type INTEGER,
    is_from_me BOOLEAN DEFAULT 0,
    guid TEXT UNIQUE NOT NULL,
    FOREIGN KEY (original_message_guid) REFERENCES messages(guid),
    FOREIGN KEY (sender_id) REFERENCES contacts(id),
    FOREIGN KEY (chat_id) REFERENCES chats(id)
);

CREATE INDEX IF NOT EXISTS idx_reactions_message_guid ON reactions(original_message_guid);
CREATE INDEX IF NOT EXISTS idx_reactions_timestamp ON reactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_reactions_sender ON reactions(sender_id);
CREATE INDEX IF NOT EXISTS idx_reactions_guid ON reactions(guid);

-- Attachments table
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    file_name TEXT,
    mime_type TEXT,
    size INTEGER,
    created_date TIMESTAMP,
    is_sticker BOOLEAN DEFAULT 0,
    guid TEXT UNIQUE NOT NULL,
    uti TEXT,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE INDEX IF NOT EXISTS idx_attachments_message_id ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_created_date ON attachments(created_date);
CREATE INDEX IF NOT EXISTS idx_attachments_guid ON attachments(guid);

-- Prompt templates (for analysis)
CREATE TABLE IF NOT EXISTS prompt_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    template_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Conversation analyses (results)
CREATE TABLE IF NOT EXISTS conversation_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    prompt_template_id INTEGER,
    eve_prompt_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    temporal_workflow_id TEXT,
    completion_id INTEGER,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (prompt_template_id) REFERENCES prompt_templates(id),
    UNIQUE(conversation_id, prompt_template_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_analyses_conversation_id ON conversation_analyses(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_analyses_prompt_template_id ON conversation_analyses(prompt_template_id);
CREATE INDEX IF NOT EXISTS idx_conversation_analyses_eve_prompt_id ON conversation_analyses(eve_prompt_id);
CREATE INDEX IF NOT EXISTS idx_conversation_analyses_status ON conversation_analyses(status);

-- Completions (LLM outputs)
CREATE TABLE IF NOT EXISTS completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    chat_id INTEGER,
    contact_id INTEGER,
    prompt_template_id INTEGER,
    compiled_prompt_text TEXT,
    model TEXT,
    result TEXT, -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (prompt_template_id) REFERENCES prompt_templates(id)
);

CREATE INDEX IF NOT EXISTS idx_completions_conversation_id ON completions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_completions_chat_id ON completions(chat_id);
CREATE INDEX IF NOT EXISTS idx_completions_prompt_template_id ON completions(prompt_template_id);
