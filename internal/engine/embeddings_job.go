package engine

import (
	"context"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"strings"

	"github.com/tylerchilds/eve/internal/db"
	"github.com/tylerchilds/eve/internal/encoding"
	"github.com/tylerchilds/eve/internal/gemini"
	"github.com/tylerchilds/eve/internal/queue"
)

// EmbeddingJobPayload represents the payload for an embedding job
type EmbeddingJobPayload struct {
	EntityType     string `json:"entity_type"`
	EntityID       int    `json:"entity_id"`
	ConversationID int    `json:"conversation_id,omitempty"`
}

// NewEmbeddingJobHandler creates a handler for embedding jobs
func NewEmbeddingJobHandler(warehouseDB *sql.DB, geminiClient *gemini.Client, model string) func(context.Context, *queue.Job) error {
	return NewEmbeddingJobHandlerWithPipeline(warehouseDB, geminiClient, model, nil)
}

// NewEmbeddingJobHandlerWithPipeline creates a handler for embedding jobs that can optionally
// serialize DB writes through a micro-batched writer.
func NewEmbeddingJobHandlerWithPipeline(warehouseDB *sql.DB, geminiClient *gemini.Client, model string, writer *TxBatchWriter) func(context.Context, *queue.Job) error {
	return func(ctx context.Context, job *queue.Job) error {
		// Parse payload
		var payload EmbeddingJobPayload
		if err := json.Unmarshal([]byte(job.PayloadJSON), &payload); err != nil {
			return fmt.Errorf("failed to parse embedding job payload: %w", err)
		}

		// Get text to embed based on entity type
		text, err := getEntityText(ctx, warehouseDB, payload.EntityType, payload.EntityID)
		if err != nil {
			return fmt.Errorf("failed to get entity text: %w", err)
		}

		// Call Gemini embeddings API
		req := gemini.EmbedContentRequest{
			Model: model,
			Content: gemini.Content{
				Parts: []gemini.Part{
					{Text: text},
				},
			},
		}

		resp, err := geminiClient.EmbedContentWithContext(ctx, &req)
		if err != nil {
			return fmt.Errorf("failed to generate embedding: %w", err)
		}

		if resp.Embedding == nil || len(resp.Embedding.Values) == 0 {
			return fmt.Errorf("empty embedding response")
		}

		// Convert float64 slice to blob (binary format)
		embeddingBlob, err := float64SliceToBlob(resp.Embedding.Values)
		if err != nil {
			return fmt.Errorf("failed to encode embedding: %w", err)
		}

		apply := func(tx *sql.Tx) error {
			_, err := tx.Exec(`
				INSERT INTO embeddings (entity_type, entity_id, model, embedding_blob, dimension)
				VALUES (?, ?, ?, ?, ?)
				ON CONFLICT(entity_type, entity_id, model) DO UPDATE SET
					embedding_blob = excluded.embedding_blob,
					dimension = excluded.dimension,
					created_at = CURRENT_TIMESTAMP
			`, payload.EntityType, payload.EntityID, model, embeddingBlob, len(resp.Embedding.Values))
			if err != nil {
				return fmt.Errorf("failed to persist embedding: %w", err)
			}
			return nil
		}

		if writer != nil {
			return writer.Submit(ctx, apply)
		}

		// Fallback: direct write (autocommit)
		tx, err := warehouseDB.BeginTx(ctx, nil)
		if err != nil {
			return fmt.Errorf("failed to begin transaction: %w", err)
		}
		defer tx.Rollback()
		if err := apply(tx); err != nil {
			return err
		}
		if err := tx.Commit(); err != nil {
			return fmt.Errorf("failed to commit embedding transaction: %w", err)
		}
		return nil
	}
}

// getEntityText retrieves the text to embed based on entity type and ID
func getEntityText(ctx context.Context, warehouseDB *sql.DB, entityType string, entityID int) (string, error) {
	switch entityType {
	case "conversation":
		// Read conversation and encode it
		reader := db.NewConversationReader(warehouseDB)
		convo, err := reader.GetConversation(entityID)
		if err != nil {
			return "", fmt.Errorf("failed to read conversation: %w", err)
		}

		// Encode conversation to text
		opts := encoding.DefaultEncodeOptions()
		opts.IncludeSendTime = true
		text := encoding.EncodeConversation(*convo, opts)
		return text, nil

	case "message":
		// Read single message text
		var text string
		err := warehouseDB.QueryRowContext(ctx, "SELECT content FROM messages WHERE id = ?", entityID).Scan(&text)
		if err != nil {
			return "", fmt.Errorf("failed to read message: %w", err)
		}
		return text, nil

	case "chat":
		// For chats, we might embed a summary or recent messages
		// For now, just return chat name or identifier
		var chatName sql.NullString
		err := warehouseDB.QueryRowContext(ctx, "SELECT chat_name FROM chats WHERE id = ?", entityID).Scan(&chatName)
		if err != nil {
			return "", fmt.Errorf("failed to read chat: %w", err)
		}
		if chatName.Valid {
			return chatName.String, nil
		}
		return fmt.Sprintf("Chat %d", entityID), nil

	case "entity":
		var title string
		if err := warehouseDB.QueryRowContext(ctx, "SELECT title FROM entities WHERE id = ?", entityID).Scan(&title); err != nil {
			return "", fmt.Errorf("failed to read entity: %w", err)
		}
		title = strings.TrimSpace(title)
		if title == "" {
			return "", fmt.Errorf("empty entity title")
		}
		return "entity: " + title, nil

	case "topic":
		var title string
		if err := warehouseDB.QueryRowContext(ctx, "SELECT title FROM topics WHERE id = ?", entityID).Scan(&title); err != nil {
			return "", fmt.Errorf("failed to read topic: %w", err)
		}
		title = strings.TrimSpace(title)
		if title == "" {
			return "", fmt.Errorf("empty topic title")
		}
		return "topic: " + title, nil

	case "emotion":
		var emotionType string
		if err := warehouseDB.QueryRowContext(ctx, "SELECT emotion_type FROM emotions WHERE id = ?", entityID).Scan(&emotionType); err != nil {
			return "", fmt.Errorf("failed to read emotion: %w", err)
		}
		emotionType = strings.TrimSpace(emotionType)
		if emotionType == "" {
			return "", fmt.Errorf("empty emotion_type")
		}
		return "emotion: " + emotionType, nil

	case "humor_item":
		var snippet string
		if err := warehouseDB.QueryRowContext(ctx, "SELECT snippet FROM humor_items WHERE id = ?", entityID).Scan(&snippet); err != nil {
			return "", fmt.Errorf("failed to read humor_item: %w", err)
		}
		snippet = strings.TrimSpace(snippet)
		if snippet == "" {
			return "", fmt.Errorf("empty humor snippet")
		}
		return "humor: " + snippet, nil

	default:
		return "", fmt.Errorf("unsupported entity type: %s", entityType)
	}
}

// float64SliceToBlob converts a float64 slice to a byte slice (little-endian)
func float64SliceToBlob(values []float64) ([]byte, error) {
	blob := make([]byte, len(values)*8)
	for i, v := range values {
		bits := math.Float64bits(v)
		binary.LittleEndian.PutUint64(blob[i*8:(i+1)*8], bits)
	}
	return blob, nil
}

// blobToFloat64Slice converts a byte slice back to float64 slice
func blobToFloat64Slice(blob []byte) ([]float64, error) {
	if len(blob)%8 != 0 {
		return nil, fmt.Errorf("invalid blob length: %d (must be multiple of 8)", len(blob))
	}

	values := make([]float64, len(blob)/8)
	for i := 0; i < len(values); i++ {
		bits := binary.LittleEndian.Uint64(blob[i*8 : (i+1)*8])
		values[i] = math.Float64frombits(bits)
	}
	return values, nil
}
