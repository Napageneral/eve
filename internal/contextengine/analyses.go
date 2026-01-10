package contextengine

import (
	"database/sql"
	"fmt"
	"sort"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// analysesContextDataAdapter retrieves analysis context (topics, entities, emotions, humor) from the database
// Supports chat_ids, contact_ids, time filtering, ordering, token budget, include filters
func analysesContextDataAdapter(params map[string]interface{}, context RetrievalContext) (RetrievalResult, error) {
	// Parse parameters
	chatIDs := parseIntSlice(params["chat_ids"])
	contactIDs := parseIntSlice(params["contact_ids"])
	timeParams := parseTimeParams(params["time"])
	tokenMax := parseInt(params["token_max"], 10000)
	order := parseString(params["order"], "timeAsc")
	include := parseIncludeList(params["include"])

	// Open database
	if context.DBPath == "" {
		return RetrievalResult{}, fmt.Errorf("analyses_context_data requires database path in context")
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
		return RetrievalResult{Text: "", ActualTokens: 0}, nil
	}

	// Filter conversations by time and chat IDs
	convIDs, err := filterConversationsByTime(db, allChatIDs, timeParams.StartISO, timeParams.EndISO)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to filter conversations: %w", err)
	}

	if len(convIDs) == 0 {
		return RetrievalResult{Text: "", ActualTokens: 0}, nil
	}

	// Order conversations
	convIDs, err = orderConversations(db, convIDs, order)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to order conversations: %w", err)
	}

	// Load analysis data for each conversation
	text, actualTokens, err := loadAndFormatAnalyses(db, convIDs, include, tokenMax)
	if err != nil {
		return RetrievalResult{}, fmt.Errorf("failed to load analyses: %w", err)
	}

	return RetrievalResult{Text: text, ActualTokens: actualTokens}, nil
}

// parseIncludeList extracts the include filter list
func parseIncludeList(val interface{}) []string {
	defaultInclude := []string{"summary", "topics", "entities", "emotions", "humor"}

	if val == nil {
		return defaultInclude
	}

	if slice, ok := val.([]interface{}); ok {
		result := make([]string, 0, len(slice))
		for _, v := range slice {
			if str, ok := v.(string); ok {
				result = append(result, str)
			}
		}
		if len(result) > 0 {
			return result
		}
	}

	if slice, ok := val.([]string); ok {
		if len(slice) > 0 {
			return slice
		}
	}

	return defaultInclude
}

// analysisRow represents aggregated analysis data for a conversation
type analysisRow struct {
	ConversationID      int64
	ConvStartDate       string
	ConversationSummary string
	Topics              []string
	Entities            []string
	Emotions            []string
	Humor               []string
}

// loadAndFormatAnalyses loads analysis data for conversations and formats them
func loadAndFormatAnalyses(db *sql.DB, convIDs []int64, include []string, tokenMax int) (string, int, error) {
	if len(convIDs) == 0 {
		return "", 0, nil
	}

	// Load analysis data for all conversations
	analysisData, err := loadAnalysisData(db, convIDs)
	if err != nil {
		return "", 0, err
	}

	// Format each conversation's analysis
	var outLines []string
	totalTokens := 0

	for _, convID := range convIDs {
		row, ok := analysisData[convID]
		if !ok {
			continue
		}

		textBlock := formatAnalysisBlock(row, include)
		tokens := len(textBlock) / 4

		if totalTokens+tokens > tokenMax {
			break
		}

		outLines = append(outLines, textBlock)
		totalTokens += tokens
	}

	result := strings.Join(outLines, "\n")
	return result, totalTokens, nil
}

// loadAnalysisData loads all analysis data for the given conversations
func loadAnalysisData(db *sql.DB, convIDs []int64) (map[int64]analysisRow, error) {
	result := make(map[int64]analysisRow)

	if len(convIDs) == 0 {
		return result, nil
	}

	// Load conversation metadata
	placeholders := make([]string, len(convIDs))
	args := make([]interface{}, len(convIDs))
	for i, id := range convIDs {
		placeholders[i] = "?"
		args[i] = id
	}

	query := fmt.Sprintf(`SELECT id, start_time, summary FROM conversations WHERE id IN (%s)`, strings.Join(placeholders, ", "))

	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, err
	}

	for rows.Next() {
		var convID int64
		var startTime string
		var summary sql.NullString

		if err := rows.Scan(&convID, &startTime, &summary); err != nil {
			rows.Close()
			return nil, err
		}

		result[convID] = analysisRow{
			ConversationID:      convID,
			ConvStartDate:       startTime,
			ConversationSummary: summary.String,
			Topics:              []string{},
			Entities:            []string{},
			Emotions:            []string{},
			Humor:               []string{},
		}
	}
	rows.Close()

	// Load facet data
	loadFacet := func(table, column string, callback func(int64, string)) error {
		if len(convIDs) == 0 {
			return nil
		}

		q := fmt.Sprintf(`SELECT conversation_id, %s FROM %s WHERE conversation_id IN (%s)`, column, table, strings.Join(placeholders, ", "))
		rows, err := db.Query(q, args...)
		if err != nil {
			return nil // Table might not exist
		}
		defer rows.Close()

		for rows.Next() {
			var convID int64
			var value sql.NullString
			if err := rows.Scan(&convID, &value); err != nil {
				return err
			}
			if value.Valid && value.String != "" {
				callback(convID, value.String)
			}
		}
		return rows.Err()
	}

	loadFacet("topics", "title", func(convID int64, value string) {
		if row, ok := result[convID]; ok {
			row.Topics = append(row.Topics, value)
			result[convID] = row
		}
	})

	loadFacet("entities", "title", func(convID int64, value string) {
		if row, ok := result[convID]; ok {
			row.Entities = append(row.Entities, value)
			result[convID] = row
		}
	})

	loadFacet("emotions", "emotion_type", func(convID int64, value string) {
		if row, ok := result[convID]; ok {
			row.Emotions = append(row.Emotions, value)
			result[convID] = row
		}
	})

	loadFacet("humor", "description", func(convID int64, value string) {
		if row, ok := result[convID]; ok {
			row.Humor = append(row.Humor, value)
			result[convID] = row
		}
	})

	return result, nil
}

// formatAnalysisBlock formats a single conversation's analysis
func formatAnalysisBlock(row analysisRow, include []string) string {
	var lines []string

	date := row.ConvStartDate
	if len(date) > 10 {
		date = date[:10]
	}
	lines = append(lines, fmt.Sprintf("### %s — Conversation %d", date, row.ConversationID))

	if containsInclude(include, "summary") && row.ConversationSummary != "" {
		lines = append(lines, fmt.Sprintf("Summary: %s", row.ConversationSummary))
	}

	if containsInclude(include, "topics") && len(row.Topics) > 0 {
		lines = append(lines, fmt.Sprintf("Topics: %s", strings.Join(uniqueSorted(row.Topics), ", ")))
	}

	if containsInclude(include, "entities") && len(row.Entities) > 0 {
		lines = append(lines, fmt.Sprintf("Entities: %s", strings.Join(uniqueSorted(row.Entities), ", ")))
	}

	if containsInclude(include, "emotions") && len(row.Emotions) > 0 {
		lines = append(lines, fmt.Sprintf("Emotions: %s", strings.Join(uniqueSorted(row.Emotions), ", ")))
	}

	if containsInclude(include, "humor") && len(row.Humor) > 0 {
		lines = append(lines, "Humor:")
		for i, h := range row.Humor {
			if i >= 10 {
				break
			}
			lines = append(lines, fmt.Sprintf("  - %s", h))
		}
	}

	return strings.Join(lines, "\n") + "\n"
}

func containsInclude(include []string, item string) bool {
	for _, i := range include {
		if i == item {
			return true
		}
	}
	return false
}

func uniqueSorted(items []string) []string {
	uniqueMap := make(map[string]bool)
	for _, item := range items {
		if item != "" {
			uniqueMap[item] = true
		}
	}

	unique := make([]string, 0, len(uniqueMap))
	for item := range uniqueMap {
		unique = append(unique, item)
	}

	sort.Strings(unique)
	return unique
}

// Simple parameter parsing helpers (duplicated from convos.go for independence)
func parseIntSlice(val interface{}) []int64 {
	if val == nil {
		return []int64{}
	}
	if slice, ok := val.([]interface{}); ok {
		result := make([]int64, 0, len(slice))
		for _, v := range slice {
			if num, ok := v.(float64); ok {
				result = append(result, int64(num))
			}
		}
		return result
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

// Database helper functions (simplified versions for analyses adapter)
func collectChatIDsFromContacts(db *sql.DB, chatIDs []int64, contactIDs []int64) ([]int64, error) {
	ids := make(map[int64]bool)
	for _, id := range chatIDs {
		ids[id] = true
	}

	if len(contactIDs) > 0 {
		placeholders := make([]string, len(contactIDs))
		args := make([]interface{}, len(contactIDs))
		for i, id := range contactIDs {
			placeholders[i] = "?"
			args[i] = id
		}
		query := fmt.Sprintf(`SELECT DISTINCT chat_id FROM chat_participants WHERE contact_id IN (%s)`, strings.Join(placeholders, ", "))
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

	result := make([]int64, 0, len(ids))
	for id := range ids {
		result = append(result, id)
	}
	return result, nil
}

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

	query := fmt.Sprintf(`SELECT id FROM conversations WHERE chat_id IN (%s) AND datetime(start_time) >= datetime(?) AND datetime(start_time) < datetime(?)`, strings.Join(placeholders, ", "))
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

func orderConversations(db *sql.DB, convIDs []int64, order string) ([]int64, error) {
	if len(convIDs) == 0 {
		return []int64{}, nil
	}

	sqlOrder := "ASC"
	if order == "timeDesc" {
		sqlOrder = "DESC"
	}

	placeholders := make([]string, len(convIDs))
	args := make([]interface{}, len(convIDs))
	for i, id := range convIDs {
		placeholders[i] = "?"
		args[i] = id
	}

	query := fmt.Sprintf(`SELECT id FROM conversations WHERE id IN (%s) ORDER BY start_time %s`, strings.Join(placeholders, ", "), sqlOrder)
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

func parseTimeParams(timeInterface interface{}) struct{ StartISO, EndISO string } {
	result := struct{ StartISO, EndISO string }{
		StartISO: "1970-01-01T00:00:00Z",
		EndISO:   "3000-01-01T00:00:00Z",
	}

	timeMap, ok := timeInterface.(map[string]interface{})
	if !ok {
		return result
	}

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
			return result
		default:
			return result
		}
		result.StartISO = start.Format(time.RFC3339)
		result.EndISO = end.Format(time.RFC3339)
		return result
	}

	if startDate, ok := timeMap["start_date"].(string); ok && startDate != "" {
		result.StartISO = startDate
	}
	if endDate, ok := timeMap["end_date"].(string); ok && endDate != "" {
		result.EndISO = endDate
	}

	return result
}
