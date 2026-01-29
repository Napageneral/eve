package etl

import (
	"database/sql"
	"fmt"
	"time"
)

// GroupAction represents a group membership change from chat.db
type GroupAction struct {
	ROWID             int64
	GUID              string
	HandleID          sql.NullInt64
	OtherHandleID     sql.NullInt64
	GroupActionType   int64
	ItemType          sql.NullInt64
	MessageActionType sql.NullInt64
	GroupTitle        sql.NullString
	Date              int64
	IsFromMe          bool
	ChatID            int64
	ChatIdentifier    string
}

// GetGroupActions reads group membership events from chat.db
func (c *ChatDB) GetGroupActions(sinceRowID int64) ([]GroupAction, error) {
	query := `
		SELECT
			m.ROWID,
			m.guid,
			m.handle_id,
			m.other_handle,
			m.group_action_type,
			m.item_type,
			m.message_action_type,
			m.group_title,
			m.date,
			m.is_from_me,
			cmj.chat_id,
			c.chat_identifier
		FROM message m
		INNER JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
		INNER JOIN chat c ON c.ROWID = cmj.chat_id
		WHERE m.ROWID > ?
		  AND m.group_action_type IS NOT NULL
		  AND m.group_action_type != 0
		ORDER BY m.ROWID
	`

	rows, err := c.db.Query(query, sinceRowID)
	if err != nil {
		return nil, fmt.Errorf("failed to query group actions: %w", err)
	}
	defer rows.Close()

	var actions []GroupAction
	for rows.Next() {
		var a GroupAction
		if err := rows.Scan(
			&a.ROWID,
			&a.GUID,
			&a.HandleID,
			&a.OtherHandleID,
			&a.GroupActionType,
			&a.ItemType,
			&a.MessageActionType,
			&a.GroupTitle,
			&a.Date,
			&a.IsFromMe,
			&a.ChatID,
			&a.ChatIdentifier,
		); err != nil {
			return nil, fmt.Errorf("failed to scan group action: %w", err)
		}
		actions = append(actions, a)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating group actions: %w", err)
	}

	return actions, nil
}

// SyncMembershipEvents copies group membership events from chat.db to eve.db
// Returns the number of membership events synced
func SyncMembershipEvents(chatDB *ChatDB, warehouseDB *sql.DB, sinceRowID int64) (int, error) {
	actions, err := chatDB.GetGroupActions(sinceRowID)
	if err != nil {
		return 0, fmt.Errorf("failed to read group actions: %w", err)
	}

	if len(actions) == 0 {
		return 0, nil
	}

	tx, err := warehouseDB.Begin()
	if err != nil {
		return 0, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	chatMap, err := loadWarehouseChatMap(tx)
	if err != nil {
		return 0, err
	}

	handleMap, err := loadWarehouseHandleMap(tx, chatDB)
	if err != nil {
		return 0, err
	}

	created := 0
	for _, action := range actions {
		if err := insertMembershipEvent(tx, chatMap, handleMap, &action); err != nil {
			return 0, fmt.Errorf("failed to insert membership event %d: %w", action.ROWID, err)
		}
		created++
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("failed to commit transaction: %w", err)
	}

	return created, nil
}

func insertMembershipEvent(tx *sql.Tx, chatMap map[string]int64, handleMap map[int64]int64, action *GroupAction) error {
	warehouseChatID, ok := chatMap[action.ChatIdentifier]
	if !ok {
		return fmt.Errorf("failed to map chat_identifier to warehouse chat id (chat_identifier=%q)", action.ChatIdentifier)
	}

	// Convert Apple timestamp to Go time
	appleEpoch := time.Date(2001, 1, 1, 0, 0, 0, 0, time.UTC)
	timestamp := appleEpoch.Add(time.Duration(action.Date) * time.Nanosecond)

	resolveContactID := func(handleID sql.NullInt64) *int64 {
		if !handleID.Valid || handleID.Int64 <= 0 {
			return nil
		}
		if contactID, ok := handleMap[handleID.Int64]; ok {
			return &contactID
		}
		return nil
	}

	var actorID *int64
	var memberID *int64
	handleID := int64(0)
	if action.HandleID.Valid {
		handleID = action.HandleID.Int64
	}

	if handleID == 0 {
		if action.IsFromMe {
			// "Me" performed the action, member is other_handle.
			memberID = resolveContactID(action.OtherHandleID)
		} else {
			// "Me" is the member, actor is other_handle.
			actorID = resolveContactID(action.OtherHandleID)
		}
	} else {
		actorID = resolveContactID(action.HandleID)
		memberID = resolveContactID(action.OtherHandleID)
	}

	groupTitle := ""
	if action.GroupTitle.Valid {
		groupTitle = action.GroupTitle.String
	}

	query := `
		INSERT INTO membership_events (
			chat_id,
			actor_id,
			member_id,
			action_type,
			item_type,
			message_action_type,
			group_title,
			timestamp,
			is_from_me,
			guid
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(guid) DO UPDATE SET
			chat_id = excluded.chat_id,
			actor_id = excluded.actor_id,
			member_id = excluded.member_id,
			action_type = excluded.action_type,
			item_type = excluded.item_type,
			message_action_type = excluded.message_action_type,
			group_title = excluded.group_title,
			timestamp = excluded.timestamp,
			is_from_me = excluded.is_from_me
	`

	if _, err := tx.Exec(query,
		warehouseChatID,
		actorID,
		memberID,
		action.GroupActionType,
		nullInt64(action.ItemType),
		nullInt64(action.MessageActionType),
		groupTitle,
		timestamp,
		action.IsFromMe,
		action.GUID,
	); err != nil {
		return fmt.Errorf("failed to insert membership event: %w", err)
	}

	return nil
}

func loadWarehouseHandleMap(tx *sql.Tx, chatDB *ChatDB) (map[int64]int64, error) {
	handles, err := chatDB.GetHandles()
	if err != nil {
		return nil, fmt.Errorf("failed to read handles: %w", err)
	}

	stmt, err := tx.Prepare(`
		SELECT contact_id
		FROM contact_identifiers
		WHERE identifier = ? AND type = ?
		LIMIT 1
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to prepare handle lookup: %w", err)
	}
	defer stmt.Close()

	handleMap := make(map[int64]int64, len(handles))
	for _, handle := range handles {
		normalized, identifierType := normalizeIdentifier(handle.ID)
		if normalized == "" {
			continue
		}
		var contactID int64
		if err := stmt.QueryRow(normalized, identifierType).Scan(&contactID); err == nil {
			handleMap[handle.ROWID] = contactID
		}
	}
	return handleMap, nil
}

func nullInt64(val sql.NullInt64) interface{} {
	if val.Valid {
		return val.Int64
	}
	return nil
}
