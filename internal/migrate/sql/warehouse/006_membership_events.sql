-- Group membership events extracted from chat.db
CREATE TABLE IF NOT EXISTS membership_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    actor_id INTEGER,
    member_id INTEGER,
    action_type INTEGER NOT NULL,
    item_type INTEGER,
    message_action_type INTEGER,
    group_title TEXT,
    timestamp TIMESTAMP NOT NULL,
    is_from_me BOOLEAN DEFAULT 0,
    guid TEXT UNIQUE NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (actor_id) REFERENCES contacts(id),
    FOREIGN KEY (member_id) REFERENCES contacts(id)
);

CREATE INDEX IF NOT EXISTS idx_membership_events_chat_id ON membership_events(chat_id);
CREATE INDEX IF NOT EXISTS idx_membership_events_timestamp ON membership_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_membership_events_member ON membership_events(member_id);
CREATE INDEX IF NOT EXISTS idx_membership_events_guid ON membership_events(guid);
