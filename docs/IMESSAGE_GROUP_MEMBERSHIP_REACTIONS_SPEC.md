# iMessage Group Membership + Reactions Spec (Eve -> Cortex)

Status: draft  
Last updated: 2026-01-27

## Goals

1) Capture group membership changes (X added Y, Y left) from chat.db.  
2) Ingest reactions as their own event type.  
3) Preserve attachments and render them correctly in Cortex episode encoding.  
4) Make verification reproducible end-to-end (wipe, reimport, encode).

## Data Sources (chat.db)

### Membership and group actions

`message` table fields of interest:
- `group_action_type` (non-zero indicates membership events)
- `other_handle` (handle involved in the action)
- `item_type` / `message_action_type` (supplementary action info)
- `group_title` (used for rename events)

Notes from local chat.db:
- `group_action_type` has non-zero values (observed: 1, 3)
- `other_handle` exists on some of those rows
- `is_service_message` appears unused (0 rows)

### Reactions

Reaction rows are identified by:
- `associated_message_guid` (not null)
- `associated_message_type` or `type` (2000-2005 legacy)  
- `text` patterns (e.g., "Loved ...", "Laughed at ...")
- `associated_message_emoji` can exist

### Attachments

Attachments live in `attachment` table and are linked to `message` via GUID.

## Eve Ingestion (Comms schema)

### A) Membership events

Add a membership extraction path in `imessage` sync:

1) Extend chat.db message query to fetch:
   - `group_action_type`, `other_handle`, `group_title`, `item_type`, `message_action_type`
2) Create a new in-memory record:
   ```
   type GroupAction struct {
     GUID, ChatIdentifier, OtherHandleID, ActionType, ItemType, MessageActionType, GroupTitle, Date, IsFromMe
   }
   ```
3) Map `group_action_type` to a semantic action (needs validation):
   - 1 -> added
   - 3 -> removed/left
4) Insert into Cortex events table (Comms schema) as a membership event:
   - `content_types`: `["membership"]`
   - `content`: optional human-readable summary
   - `metadata_json`:
     ```
     {
       "action": "added" | "removed",
       "group_action_type": 1,
       "item_type": 1,
       "message_action_type": 0,
       "other_handle_id": 123,
       "other_contact_id": "cortex-contact-id",
       "group_title": "Coed Coven"
     }
     ```
5) Add `event_participants`:
   - sender: actor (if known)
   - member: add `role = "member"` (new role) or store in metadata only

### B) Reactions

Current issue: reactions are written as normal text messages before `syncReactions` runs.
Because the event ID is identical (GUID), the reaction insert is ignored.

Fix options:
1) **Skip reaction messages** in `syncMessages`:
   - If `associated_message_guid` present and `associated_message_type` in 2000-2005
   - Or if text starts with "Loved", "Liked", "Laughed at", "Emphasized", "Questioned", "Disliked"
2) **Upgrade existing event** in `syncReactions`:
   - `UPDATE events SET content_types='["reaction"]', content='<emoji>', reply_to='<original_guid>'`

Reaction event payload:
- `content_types`: `["reaction"]`
- `content`: emoji (or string form)
- `reply_to`: original message GUID
- `metadata_json`:
  ```
  {
    "reaction_type": 2000,
    "reaction_text": "Loved ...",
    "associated_message_guid": "...",
    "associated_message_type": 2000,
    "associated_message_emoji": "<emoji>"
  }
  ```

### C) Attachments

Attachments already sync to the `attachments` table, but encoding misses them because
events use `content_types=["attachment"]` rather than a media-specific type.

Fix:
- Add a media hint to `content_types` for image/video/audio (e.g., `["attachment","image"]`)
- Or store attachment metadata in `metadata_json`:
  ```
  {
    "attachment_guid": "...",
    "mime_type": "image/jpeg",
    "file_name": "...",
    "is_sticker": false
  }
  ```

## Cortex Episode Encoding

### Participants (group chats)

Goal: show **everyone who could read the episode**.

Approach:
1) Build a membership timeline using membership events.
2) At episode start, compute active participant set.
3) Include that set in `<EPISODE_CONTEXT>`.
4) If join/leave occurs inside the episode, update the set and optionally insert a
   membership line in `<MESSAGES>`.

### Reactions

Render reactions as reactions, not full messages:
```
  -> <reactor> <emoji> to "<snippet>"
```
If the reacted-to message is not in the episode, render:
```
  -> <reactor> reacted <emoji>
```

### Attachments

Render attachments using metadata/attachment table:
```
[timestamp] <sender>: [Attachment] <file_name> (<mime_type>)
```
For images:
```
[timestamp] <sender>: [Image]
```

## Verification Plan (End-to-End)

1) **Backup** `cortex.db` and Eve warehouse DB.
2) **Wipe imessage data in Cortex** (example SQL):
   ```
   BEGIN;
   DELETE FROM attachments WHERE event_id IN (SELECT id FROM events WHERE channel='imessage');
   DELETE FROM event_participants WHERE event_id IN (SELECT id FROM events WHERE channel='imessage');
   DELETE FROM event_tags WHERE event_id IN (SELECT id FROM events WHERE channel='imessage');
   DELETE FROM events WHERE channel='imessage';
   DELETE FROM threads WHERE channel='imessage';
   DELETE FROM sync_watermarks WHERE adapter='imessage';
   COMMIT;
   ```
3) **Wipe Eve warehouse** (delete Eve DB or truncate tables for imessage data).
4) **Full reimport** via Cortex iMessage sync.
5) **Verify**:
   - Select a group chat with join/leave actions.
   - Run `verify-memory-live` with debug.
   - Confirm membership events appear and participants reflect membership state.
   - Confirm reactions are `content_types=["reaction"]` and encoded as reactions.

## Open Questions

- Confirm the mapping of `group_action_type` values to actions (1 vs 3).
- Confirm `other_handle` always identifies the member for add/remove events.
- Decide whether to store membership events in a dedicated table or in events with `content_types=["membership"]`.
