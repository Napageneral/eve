package contextengine

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// convosContextDataAdapter retrieves conversation context from the database
// Supports chat_ids, contact_ids, time filtering, ordering, token budget
func convosContextDataAdapter(params map[string]interface{}, context RetrievalContext) (RetrievalResult, error) {
	// Parse parameters
	chatIDs := parseIntSlice(params["chat_ids"])
	contactIDs := parseIntSlice(params["contact_ids"])
	timeParams := parseTimeParams(params["time"])
	tokenMax := parseInt(params["token_max"], 10000)
	order := parseString(params["order"], "timeAsc")

	// TODO: match (entities, topics, emotions) filtering - deferred for v1
	// TODO: encode options (include_sender, include_attachments, include_reactions) - using defaults for v1
	// TODO: similarity ordering - deferred for v1

	// Open database
	if context.DBPath == "" {
		return RetrievalResult{}, fmt.Errorf("convos_context_data requires database path in context")
	}

	db, err := sql.Open("sqlite3", context.DBPath)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to open database: %w", err)
	}
	defer db.Close()

	// Collect chat IDs (expand contact_ids to chat_ids)
	allChatIDs, err := collectChatIDsFromContacts(db, chatIDs, contactIDs)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to collect chat IDs: %w", err)
	}

	if len(allChatIDs) == 0 {
		// No chats to query, return empty
		return RetrievalResult{
			Text:         "",
			ActualTokens: 0,
		}, nil
	}

	// Filter conversations by time and chat IDs
	convIDs, err := filterConversationsByTime(db, allChatIDs, timeParams.StartISO, timeParams.EndISO)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to filter conversations: %w", err)
	}

	if len(convIDs) == 0 {
		// No conversations in time range
		return RetrievalResult{
			Text:         "",
			ActualTokens: 0,
		}, nil
	}

	// Order conversations
	convIDs, err = orderConversations(db, convIDs, order)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to order conversations: %w", err)
	}

	// Load and encode conversations with token budget
	text, actualTokens, err := loadAndEncodeConversations(db, convIDs, tokenMax)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to load conversations: %w", err)
	}

	return RetrievalResult{
		Text:         text,
		ActualTokens: actualTokens,
	}, nil
}

// timeParams represents time filtering parameters
type timeParams struct {
	StartISO string
	EndISO   string
}

// parseTimeParams extracts time filtering from params
func parseTimeParams(timeInterface interface{}) timeParams {
	result := timeParams{
		StartISO: "1970-01-01T00:00:00Z",
		EndISO:   "3000-01-01T00:00:00Z",
	}

	timeMap, ok := timeInterface.(map[string]interface{})
	if !ok {
		return result
	}

	// Check for preset
	if preset, ok := timeMap["preset"].(string); ok {
		end := time.Now()
		var start time.Time

		switch strings.ToLower(preset) {
		case "day":
			start = end.AddDate(0, 0, -1)
		case "week":
			start = end.AddDate(0, 0, -7)
		case "month":
			start = end.AddDate(0, 0, -30)
		case "year":
			start = end.AddDate(0, 0, -365)
		case "all":
			return result // Use default wide range
		default:
			return result
		}

		result.StartISO = start.Format(time.RFC3339)
		result.EndISO = end.Format(time.RFC3339)
		return result
	}

	// Check for explicit start/end dates
	if startDate, ok := timeMap["start_date"].(string); ok && startDate != "" {
		result.StartISO = startDate
	}
	if endDate, ok := timeMap["end_date"].(string); ok && endDate != "" {
		result.EndISO = endDate
	}

	return result
}

// collectChatIDsFromContacts collects chat IDs from direct chat_ids and expanded contact_ids
func collectChatIDsFromContacts(db *sql.DB, chatIDs []int64, contactIDs []int64) ([]int64, error) {
	ids := make(map[int64]bool)

	// Add direct chat IDs
	for _, id := range chatIDs {
		ids[id] = true
	}

	// Expand contact IDs to chat IDs
	if len(contactIDs) > 0 {
		placeholders := make([]string, len(contactIDs))
		args := make([]interface{}, len(contactIDs))
		for i, id := range contactIDs {
			placeholders[i] = "?"
			args[i] = id
		}

		query := fmt.Sprintf(`
			SELECT DISTINCT chat_id
			FROM chat_participants
			WHERE contact_id IN (%s)
		`, strings.Join(placeholders, ", "))

		rows, err := db.Query(query, args...)
		if err != nil {
			return nil, err
		}
		defer rows.Close()

		for rows.Next() {
			var chatID int64
			if err := rows.Scan(&chatID); err != nil {
				return nil, err
			}
			ids[chatID] = true
		}
	}

	// Convert map to slice
	result := make([]int64, 0, len(ids))
	for id := range ids {
		result = append(result, id)
	}

	return result, nil
}

// filterConversationsByTime filters conversations by time range and chat IDs
func filterConversationsByTime(db *sql.DB, chatIDs []int64, startISO, endISO string) ([]int64, error) {
	if len(chatIDs) == 0 {
		return []int64{}, nil
	}

	placeholders := make([]string, len(chatIDs))
	args := make([]interface{}, 0, len(chatIDs)+2)
	for i, id := range chatIDs {
		placeholders[i] = "?"
		args = append(args, id)
	}
	args = append(args, startISO, endISO)

	query := fmt.Sprintf(`
		SELECT id
		FROM conversations
		WHERE chat_id IN (%s)
		  AND datetime(start_time) >= datetime(?)
		  AND datetime(start_time) < datetime(?)
	`, strings.Join(placeholders, ", "))

	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var convIDs []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		convIDs = append(convIDs, id)
	}

	return convIDs, nil
}

// orderConversations orders conversation IDs by the specified order
func orderConversations(db *sql.DB, convIDs []int64, order string) ([]int64, error) {
	if len(convIDs) == 0 {
		return []int64{}, nil
	}

	// Determine sort order
	sqlOrder := "ASC"
	if order == "timeDesc" {
		sqlOrder = "DESC"
	}
	// TODO: similarity ordering (simAsc, simDesc) - requires vector embeddings, deferred for v1

	placeholders := make([]string, len(convIDs))
	args := make([]interface{}, len(convIDs))
	for i, id := range convIDs {
		placeholders[i] = "?"
		args[i] = id
	}

	query := fmt.Sprintf(`
		SELECT id
		FROM conversations
		WHERE id IN (%s)
		ORDER BY start_time %s
	`, strings.Join(placeholders, ", "), sqlOrder)

	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var orderedIDs []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		orderedIDs = append(orderedIDs, id)
	}

	return orderedIDs, nil
}

// loadAndEncodeConversations loads conversations, encodes them, and respects token budget
func loadAndEncodeConversations(db *sql.DB, convIDs []int64, tokenMax int) (string, int, error) {
	if len(convIDs) == 0 {
		return "", 0, nil
	}

	// First, get chat_id for each conversation
	convToChat := make(map[int64]int64)
	placeholders := make([]string, len(convIDs))
	args := make([]interface{}, len(convIDs))
	for i, id := range convIDs {
		placeholders[i] = "?"
		args[i] = id
	}

	query := fmt.Sprintf(`
		SELECT id, chat_id
		FROM conversations
		WHERE id IN (%s)
	`, strings.Join(placeholders, ", "))

	rows, err := db.Query(query, args...)
	if err != nil {
		return "", 0, err
	}
	for rows.Next() {
		var convID, chatID int64
		if err := rows.Scan(&convID, &chatID); err != nil {
			rows.Close()
			return "", 0, err
		}
		convToChat[convID] = chatID
	}
	rows.Close()

	// Load and encode each conversation
	var outLines []string
	totalTokens := 0

	for _, convID := range convIDs {
		chatID, ok := convToChat[convID]
		if !ok {
			continue
		}

		// Load conversation messages using existing encoding package
		// We'll create a temporary database file path for the encoding package
		// Actually, we already have db open, so we can use encoding.LoadConversation directly
		// But that function takes a file path. Let's use the raw SQL approach instead.

		// Load messages for this conversation
		text, err := loadConversationText(db, convID, chatID)
		if err != nil {
			// Skip conversations that fail to load
			continue
		}

		if text == "" {
			continue
		}

		// Estimate tokens
		tokens := len(text) / 4

		// Check budget
		if totalTokens+tokens > tokenMax {
			break
		}

		outLines = append(outLines, text)
		totalTokens += tokens
	}

	result := strings.Join(outLines, "\n\n")
	return result, totalTokens, nil
}

// loadConversationText loads and encodes a single conversation
func loadConversationText(db *sql.DB, convID, chatID int64) (string, error) {
	// Query messages for this conversation
	// This mirrors the encoding.LoadConversation logic
	query := `
		SELECT
			m.text,
			m.date,
			m.is_from_me,
			h.id as handle_id,
			COALESCE(c.given_name, h.id, 'Unknown') as sender_name
		FROM message m
		LEFT JOIN handle h ON m.handle_id = h.ROWID
		LEFT JOIN contact c ON h.id = c.phone_number
		WHERE m.chat_id = ?
		ORDER BY m.date ASC
	`

	rows, err := db.Query(query, chatID)
	if err != nil {
		return "", err
	}
	defer rows.Close()

	var lines []string
	for rows.Next() {
		var text sql.NullString
		var date int64
		var isFromMe int
		var handleID sql.NullString
		var senderName string

		if err := rows.Scan(&text, &date, &isFromMe, &handleID, &senderName); err != nil {
			return "", err
		}

		// Skip empty messages
		if !text.Valid || text.String == "" {
			continue
		}

		// Format: "SenderName: message text"
		if isFromMe == 1 {
			lines = append(lines, fmt.Sprintf("Me: %s", text.String))
		} else {
			lines = append(lines, fmt.Sprintf("%s: %s", senderName, text.String))
		}
	}

	return strings.Join(lines, "\n"), nil
}

// Helper functions to parse params

func parseIntSlice(val interface{}) []int64 {
	if val == nil {
		return []int64{}
	}

	// Handle []interface{} from JSON unmarshaling
	if slice, ok := val.([]interface{}); ok {
		result := make([]int64, 0, len(slice))
		for _, v := range slice {
			if num, ok := v.(float64); ok {
				result = append(result, int64(num))
			} else if num, ok := v.(int); ok {
				result = append(result, int64(num))
			} else if str, ok := v.(string); ok {
				// Try parsing string as int
				var n int64
				if _, err := fmt.Sscanf(str, "%d", &n); err == nil {
					result = append(result, n)
				}
			}
		}
		return result
	}

	// Handle []int64 directly
	if slice, ok := val.([]int64); ok {
		return slice
	}

	return []int64{}
}

func parseInt(val interface{}, defaultVal int) int {
	if val == nil {
		return defaultVal
	}

	if num, ok := val.(float64); ok {
		return int(num)
	}
	if num, ok := val.(int); ok {
		return num
	}
	if str, ok := val.(string); ok {
		var n int
		if _, err := fmt.Sscanf(str, "%d", &n); err == nil {
			return n
		}
	}

	return defaultVal
}

func parseString(val interface{}, defaultVal string) string {
	if val == nil {
		return defaultVal
	}

	if str, ok := val.(string); ok {
		return str
	}

	return defaultVal
}
