package engine

import (
	"context"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"

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

		resp, err := geminiClient.EmbedContent(&req)
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

		// Persist to embeddings table
		_, err = warehouseDB.ExecContext(ctx, `
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
