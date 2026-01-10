-- Analysis facet tables (topics/entities/emotions/humor)
-- These are populated by convo-wide analysis prompts like `convo-all-v1`.

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    contact_id INTEGER,
    title TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    UNIQUE(conversation_id, contact_id, title)
);

CREATE INDEX IF NOT EXISTS idx_entities_chat_id ON entities(chat_id);
CREATE INDEX IF NOT EXISTS idx_entities_conversation_id ON entities(conversation_id);
CREATE INDEX IF NOT EXISTS idx_entities_contact_id ON entities(contact_id);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    contact_id INTEGER,
    title TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    UNIQUE(conversation_id, contact_id, title)
);

CREATE INDEX IF NOT EXISTS idx_topics_chat_id ON topics(chat_id);
CREATE INDEX IF NOT EXISTS idx_topics_conversation_id ON topics(conversation_id);
CREATE INDEX IF NOT EXISTS idx_topics_contact_id ON topics(contact_id);

CREATE TABLE IF NOT EXISTS emotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    contact_id INTEGER,
    emotion_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    UNIQUE(conversation_id, contact_id, emotion_type)
);

CREATE INDEX IF NOT EXISTS idx_emotions_chat_id ON emotions(chat_id);
CREATE INDEX IF NOT EXISTS idx_emotions_conversation_id ON emotions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_emotions_contact_id ON emotions(contact_id);

CREATE TABLE IF NOT EXISTS humor_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    contact_id INTEGER,
    snippet TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id),
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    UNIQUE(conversation_id, contact_id, snippet)
);

CREATE INDEX IF NOT EXISTS idx_humor_items_chat_id ON humor_items(chat_id);
CREATE INDEX IF NOT EXISTS idx_humor_items_conversation_id ON humor_items(conversation_id);
CREATE INDEX IF NOT EXISTS idx_humor_items_contact_id ON humor_items(contact_id);

