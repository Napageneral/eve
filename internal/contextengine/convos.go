package contextengine

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/tylerchilds/eve/internal/encoding"
)

// convosContextDataAdapter retrieves raw conversation text with flexible filtering
// This is a DB-backed retrieval that supports chat/contact filtering, time windows,
// facet matching, and token budgeting
func convosContextDataAdapter(params map[string]interface{}, context RetrievalContext) (RetrievalResult, error) {
	// Parse parameters
	convosParams := parseConvosParams(params)

	// Get database connection
	db, err := sql.Open("sqlite3", context.DBPath)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to open database: %w", err)
	}
	defer db.Close()

	// Retrieve conversations context
	text, err := retrieveConvosContext(db, convosParams)
	if err != nil {
		return RetrievalResult{}, err
	}

	// Estimate tokens (rough approximation: length / 4)
	actualTokens := len(text) / 4

	return RetrievalResult{
		Text:         text,
		ActualTokens: actualTokens,
	}, nil
}

// ConvosParams holds parameters for conversation context retrieval
type ConvosParams struct {
	ChatIDs    []int64
	ContactIDs []int64
	Time       TimeParams
	TokenMax   int
	Order      string // "timeAsc", "timeDesc", "simAsc", "simDesc"
	Match      MatchParams
	Encode     EncodeParams
	Similarity SimilarityParams
}

// TimeParams controls time filtering
type TimeParams struct {
	Preset    string // "day", "week", "month", "year", "all"
	StartDate string // ISO string
	EndDate   string // ISO string
}

// MatchParams controls facet filtering
type MatchParams struct {
	Entities []string
	Topics   []string
	Emotions []string
}

// EncodeParams controls encoding options
type EncodeParams struct {
	IncludeSender      bool
	IncludeAttachments bool
	IncludeReactions   bool
}

// SimilarityParams controls similarity search (TODO: not implemented in v1)
type SimilarityParams struct {
	Query    string
	MinScore float64
}

// parseConvosParams extracts ConvosParams from raw params map
func parseConvosParams(params map[string]interface{}) ConvosParams {
	result := ConvosParams{
		TokenMax: 10000,
		Order:    "timeAsc",
		Encode: EncodeParams{
			IncludeSender:      true,
			IncludeAttachments: true,
			IncludeReactions:   true,
		},
	}

	// Parse chat_ids
	if chatIDs, ok := params["chat_ids"].([]interface{}); ok {
		for _, id := range chatIDs {
			switch v := id.(type) {
			case int:
				result.ChatIDs = append(result.ChatIDs, int64(v))
			case int64:
				result.ChatIDs = append(result.ChatIDs, v)
			case float64:
				result.ChatIDs = append(result.ChatIDs, int64(v))
			}
		}
	}

	// Parse contact_ids
	if contactIDs, ok := params["contact_ids"].([]interface{}); ok {
		for _, id := range contactIDs {
			switch v := id.(type) {
			case int:
				result.ContactIDs = append(result.ContactIDs, int64(v))
			case int64:
				result.ContactIDs = append(result.ContactIDs, v)
			case float64:
				result.ContactIDs = append(result.ContactIDs, int64(v))
			}
		}
	}

	// Parse time
	if timeParam, ok := params["time"].(map[string]interface{}); ok {
		if preset, ok := timeParam["preset"].(string); ok {
			result.Time.Preset = preset
		}
		if startDate, ok := timeParam["start_date"].(string); ok {
			result.Time.StartDate = startDate
		}
		if endDate, ok := timeParam["end_date"].(string); ok {
			result.Time.EndDate = endDate
		}
	}

	// Parse token_max
	if tokenMax, ok := params["token_max"]; ok {
		switch v := tokenMax.(type) {
		case int:
			result.TokenMax = v
		case int64:
			result.TokenMax = int(v)
		case float64:
			result.TokenMax = int(v)
		}
	}

	// Parse order
	if order, ok := params["order"].(string); ok {
		result.Order = order
	}

	// Parse match
	if match, ok := params["match"].(map[string]interface{}); ok {
		if entities, ok := match["entities"].([]interface{}); ok {
			for _, e := range entities {
				if s, ok := e.(string); ok {
					result.Match.Entities = append(result.Match.Entities, s)
				}
			}
		}
		if topics, ok := match["topics"].([]interface{}); ok {
			for _, t := range topics {
				if s, ok := t.(string); ok {
					result.Match.Topics = append(result.Match.Topics, s)
				}
			}
		}
		if emotions, ok := match["emotions"].([]interface{}); ok {
			for _, e := range emotions {
				if s, ok := e.(string); ok {
					result.Match.Emotions = append(result.Match.Emotions, s)
				}
			}
		}
	}

	// Parse encode
	if encode, ok := params["encode"].(map[string]interface{}); ok {
		if includeSender, ok := encode["include_sender"].(bool); ok {
			result.Encode.IncludeSender = includeSender
		}
		if includeAttachments, ok := encode["include_attachments"].(bool); ok {
			result.Encode.IncludeAttachments = includeAttachments
		}
		if includeReactions, ok := encode["include_reactions"].(bool); ok {
			result.Encode.IncludeReactions = includeReactions
		}
	}

	// Parse similarity (not implemented in v1)
	if similarity, ok := params["similarity"].(map[string]interface{}); ok {
		if query, ok := similarity["query"].(string); ok {
			result.Similarity.Query = query
		}
		if minScore, ok := similarity["min_score"].(float64); ok {
			result.Similarity.MinScore = minScore
		}
	}

	return result
}

// resolveTime resolves time window from params
func resolveTime(timeParams TimeParams) (string, string) {
	preset := strings.ToLower(timeParams.Preset)

	if preset == "day" || preset == "week" || preset == "month" || preset == "year" {
		end := time.Now()
		daysMap := map[string]int{
			"day":   1,
			"week":  7,
			"month": 30,
			"year":  365,
		}
		start := end.AddDate(0, 0, -daysMap[preset])
		return start.Format(time.RFC3339), end.Format(time.RFC3339)
	}

	if preset == "all" {
		return "1970-01-01T00:00:00Z", "3000-01-01T00:00:00Z"
	}

	startDate := timeParams.StartDate
	if startDate == "" {
		startDate = "1970-01-01T00:00:00Z"
	}
	endDate := timeParams.EndDate
	if endDate == "" {
		endDate = "3000-01-01T00:00:00Z"
	}

	return startDate, endDate
}

// getChatIDsForContact gets chat IDs for a specific contact
func getChatIDsForContact(db *sql.DB, contactID int64) ([]int64, error) {
	query := `
		SELECT DISTINCT chat_id
		FROM chat_participants
		WHERE contact_id = ?
	`
	rows, err := db.Query(query, contactID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var chatIDs []int64
	for rows.Next() {
		var chatID int64
		if err := rows.Scan(&chatID); err != nil {
			return nil, err
		}
		chatIDs = append(chatIDs, chatID)
	}

	return chatIDs, rows.Err()
}

// collectChatIDs collects chat IDs (union of chat_ids + expanded contact_ids)
func collectChatIDs(db *sql.DB, chatIDs []int64, contactIDs []int64) []int64 {
	idSet := make(map[int64]bool)

	// Add direct chat IDs
	for _, id := range chatIDs {
		idSet[id] = true
	}

	// Expand contact IDs to chat IDs
	for _, contactID := range contactIDs {
		chatIDsForContact, err := getChatIDsForContact(db, contactID)
		if err != nil {
			// Best-effort: skip on failure
			continue
		}
		for _, chatID := range chatIDsForContact {
			idSet[chatID] = true
		}
	}

	// Convert set to slice
	result := make([]int64, 0, len(idSet))
	for id := range idSet {
		result = append(result, id)
	}

	return result
}

// filterConversationIDs filters conversation IDs by time and facets
func filterConversationIDs(db *sql.DB, chatIDs []int64, startISO, endISO string, match MatchParams) ([]int64, error) {
	if len(chatIDs) == 0 {
		return []int64{}, nil
	}

	// Build base query with time + chat filter
	placeholders := make([]string, len(chatIDs))
	args := make([]interface{}, 0, len(chatIDs)+2)
	for i, id := range chatIDs {
		placeholders[i] = "?"
		args = append(args, id)
	}
	args = append(args, startISO, endISO)

	query := fmt.Sprintf(`
		SELECT id, chat_id
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

	var baseIDs []int64
	for rows.Next() {
		var id, chatID int64
		if err := rows.Scan(&id, &chatID); err != nil {
			return nil, err
		}
		baseIDs = append(baseIDs, id)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	if len(baseIDs) == 0 {
		return []int64{}, nil
	}

	// Apply facet filtering
	idSet := make(map[int64]bool)
	for _, id := range baseIDs {
		idSet[id] = true
	}

	// Helper to apply facet filtering
	applyFacet := func(table, col string, values []string) error {
		if len(values) == 0 || len(idSet) == 0 {
			return nil
		}

		// Lowercase values for case-insensitive matching
		lowerValues := make([]string, 0, len(values))
		for _, v := range values {
			if strings.TrimSpace(v) != "" {
				lowerValues = append(lowerValues, strings.ToLower(v))
			}
		}
		if len(lowerValues) == 0 {
			return nil
		}

		// Get current IDs
		ids := make([]int64, 0, len(idSet))
		for id := range idSet {
			ids = append(ids, id)
		}

		// Build query
		idPlaceholders := make([]string, len(ids))
		valPlaceholders := make([]string, len(lowerValues))
		args := make([]interface{}, 0, len(ids)+len(lowerValues))
		for i, id := range ids {
			idPlaceholders[i] = "?"
			args = append(args, id)
		}
		for i, val := range lowerValues {
			valPlaceholders[i] = "?"
			args = append(args, val)
		}

		facetQuery := fmt.Sprintf(`
			SELECT DISTINCT conversation_id
			FROM %s
			WHERE conversation_id IN (%s)
			  AND LOWER(%s) IN (%s)
		`, table, strings.Join(idPlaceholders, ", "), col, strings.Join(valPlaceholders, ", "))

		rows, err := db.Query(facetQuery, args...)
		if err != nil {
			return err
		}
		defer rows.Close()

		// Collect matching IDs
		matchingIDs := make(map[int64]bool)
		for rows.Next() {
			var id int64
			if err := rows.Scan(&id); err != nil {
				return err
			}
			matchingIDs[id] = true
		}

		// Update idSet to only include matching IDs
		idSet = matchingIDs
		return rows.Err()
	}

	// Apply each facet filter
	if err := applyFacet("entities", "title", match.Entities); err != nil {
		return nil, err
	}
	if err := applyFacet("topics", "title", match.Topics); err != nil {
		return nil, err
	}
	if err := applyFacet("emotions", "emotion_type", match.Emotions); err != nil {
		return nil, err
	}

	// Convert set to slice
	result := make([]int64, 0, len(idSet))
	for id := range idSet {
		result = append(result, id)
	}

	return result, nil
}

// orderConversationsByTime orders conversations by time
func orderConversationsByTime(db *sql.DB, convIDs []int64, asc bool) ([]int64, error) {
	if len(convIDs) == 0 {
		return []int64{}, nil
	}

	order := "ASC"
	if !asc {
		order = "DESC"
	}

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
	`, strings.Join(placeholders, ", "), order)

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

	return orderedIDs, rows.Err()
}

// loadConversationWithMessages loads a single conversation with all messages
func loadConversationWithMessages(db *sql.DB, convID, chatID int64) (*encoding.Conversation, error) {
	// Get conversation metadata
	convQuery := `
		SELECT id, chat_id, start_time, end_time
		FROM conversations
		WHERE id = ? AND chat_id = ?
		LIMIT 1
	`

	var conv encoding.Conversation
	var startTimeStr, endTimeStr string
	err := db.QueryRow(convQuery, convID, chatID).Scan(
		&conv.ID,
		&conv.ChatID,
		&startTimeStr,
		&endTimeStr,
	)
	if err != nil {
		return nil, err
	}

	// Parse times
	conv.StartTime, _ = time.Parse(time.RFC3339, startTimeStr)
	conv.EndTime, _ = time.Parse(time.RFC3339, endTimeStr)

	// Get messages for conversation
	msgQuery := `
		SELECT
			m.id,
			m.guid,
			m.timestamp,
			COALESCE(c.name, 'Unknown') as sender_name,
			COALESCE(m.text, '') as text,
			COALESCE(c.is_me, 0) as is_from_me
		FROM messages m
		LEFT JOIN contacts c ON m.sender_id = c.id
		WHERE m.conversation_id = ?
		ORDER BY m.timestamp ASC
	`

	rows, err := db.Query(msgQuery, convID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	for rows.Next() {
		var msg encoding.Message
		var timestampStr string
		var isFromMe int

		err := rows.Scan(
			&msg.ID,
			&msg.GUID,
			&timestampStr,
			&msg.SenderName,
			&msg.Text,
			&isFromMe,
		)
		if err != nil {
			return nil, err
		}

		msg.Timestamp, _ = time.Parse(time.RFC3339, timestampStr)
		msg.IsFromMe = isFromMe == 1

		// Load attachments for this message
		msg.Attachments, _ = loadAttachmentsForMessage(db, msg.ID)

		// Load reactions for this message
		msg.Reactions, _ = loadReactionsForMessage(db, msg.GUID)

		conv.Messages = append(conv.Messages, msg)
	}

	return &conv, rows.Err()
}

// loadAttachmentsForMessage loads attachments for a message
func loadAttachmentsForMessage(db *sql.DB, messageID int64) ([]encoding.Attachment, error) {
	query := `
		SELECT id, mime_type, file_name, is_sticker
		FROM attachments
		WHERE message_id = ?
	`

	rows, err := db.Query(query, messageID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var attachments []encoding.Attachment
	for rows.Next() {
		var att encoding.Attachment
		var isSticker int
		err := rows.Scan(&att.ID, &att.MimeType, &att.FileName, &isSticker)
		if err != nil {
			return nil, err
		}
		att.IsSticker = isSticker == 1
		attachments = append(attachments, att)
	}

	return attachments, rows.Err()
}

// loadReactionsForMessage loads reactions for a message
func loadReactionsForMessage(db *sql.DB, messageGUID string) ([]encoding.Reaction, error) {
	query := `
		SELECT
			r.reaction_type,
			COALESCE(c.name, 'Unknown') as sender_name,
			COALESCE(c.is_me, 0) as is_from_me
		FROM reactions r
		LEFT JOIN contacts c ON r.sender_id = c.id
		WHERE r.original_message_guid = ?
	`

	rows, err := db.Query(query, messageGUID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var reactions []encoding.Reaction
	for rows.Next() {
		var reaction encoding.Reaction
		var isFromMe int
		err := rows.Scan(&reaction.ReactionType, &reaction.SenderName, &isFromMe)
		if err != nil {
			return nil, err
		}
		reaction.IsFromMe = isFromMe == 1
		reactions = append(reactions, reaction)
	}

	return reactions, rows.Err()
}

// retrieveConvosContext retrieves conversations context
func retrieveConvosContext(db *sql.DB, params ConvosParams) (string, error) {
	// Resolve time window
	startISO, endISO := resolveTime(params.Time)

	// Collect chat IDs
	chatIDs := collectChatIDs(db, params.ChatIDs, params.ContactIDs)
	if len(chatIDs) == 0 {
		return "", nil // No chat IDs resolved
	}

	// Filter conversations
	convIDs, err := filterConversationIDs(db, chatIDs, startISO, endISO, params.Match)
	if err != nil {
		return "", err
	}
	if len(convIDs) == 0 {
		return "", nil // No conversations found
	}

	// Order conversations
	if params.Order == "timeAsc" || params.Order == "timeDesc" {
		convIDs, err = orderConversationsByTime(db, convIDs, params.Order == "timeAsc")
		if err != nil {
			return "", err
		}
	} else {
		// TODO: Implement similarity ordering
		// For now, use timeAsc as fallback
		convIDs, err = orderConversationsByTime(db, convIDs, true)
		if err != nil {
			return "", err
		}
	}

	// Map conv -> chat for hydration
	convToChat := make(map[int64]int64)
	if len(convIDs) > 0 {
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
			return "", err
		}
		defer rows.Close()

		for rows.Next() {
			var convID, chatID int64
			if err := rows.Scan(&convID, &chatID); err != nil {
				return "", err
			}
			convToChat[convID] = chatID
		}
	}

	// Load and encode conversations with token budget
	var outLines []string
	totalTokens := 0

	for _, convID := range convIDs {
		chatID, ok := convToChat[convID]
		if !ok {
			continue // Skip if no chat mapping
		}

		// Load conversation with messages
		conv, err := loadConversationWithMessages(db, convID, chatID)
		if err != nil {
			continue // Skip on error
		}
		if len(conv.Messages) == 0 {
			continue // Skip empty conversations
		}

		// Encode conversation
		opts := encoding.EncodeOptions{
			IncludeSender:      params.Encode.IncludeSender,
			IncludeAttachments: params.Encode.IncludeAttachments,
			IncludeReactions:   params.Encode.IncludeReactions,
		}
		encoded := encoding.EncodeConversation(conv, opts)

		// Count tokens
		approxTokens := len(encoded) / 4

		// Check budget
		if totalTokens+approxTokens > params.TokenMax {
			break // Stop if over budget
		}

		outLines = append(outLines, encoded)
		totalTokens += approxTokens
	}

	return strings.Join(outLines, "\n\n"), nil
}
