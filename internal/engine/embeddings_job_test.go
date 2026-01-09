package engine

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/tylerchilds/eve/internal/gemini"
	"github.com/tylerchilds/eve/internal/migrate"
	"github.com/tylerchilds/eve/internal/queue"
)

func TestEmbeddingJob_Conversation(t *testing.T) {
	// Create fake Gemini server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := gemini.EmbedContentResponse{
			Embedding: &gemini.Embedding{
				Values: []float64{0.1, 0.2, 0.3, 0.4, 0.5},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	// Create temp warehouse DB
	tmpfile, err := os.CreateTemp("", "eve-warehouse-*.db")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(tmpfile.Name())
	tmpfile.Close()

	dbPath := tmpfile.Name()

	// Run migrations
	if err := migrate.MigrateWarehouse(dbPath); err != nil {
		t.Fatalf("migration failed: %v", err)
	}

	warehouseDB, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		t.Fatal(err)
	}
	defer warehouseDB.Close()

	// Insert test data
	insertTestConversationForEmbedding(t, warehouseDB)

	// Create Gemini client with fake server
	geminiClient := gemini.NewClient("fake-key")
	geminiClient.HttpClient.Transport = &embedTestTransport{
		testServer: server,
	}

	// Create embedding job handler
	handler := NewEmbeddingJobHandler(warehouseDB, geminiClient, "gemini-embedding-001")

	// Create job
	payload := EmbeddingJobPayload{
		EntityType:     "conversation",
		EntityID:       1,
		ConversationID: 1,
	}
	payloadJSON, _ := json.Marshal(payload)
	job := &queue.Job{
		ID:          "job-1",
		Type:        "embedding",
		PayloadJSON: string(payloadJSON),
	}

	// Execute job
	ctx := context.Background()
	err = handler(ctx, job)
	if err != nil {
		t.Fatalf("job execution failed: %v", err)
	}

	// Verify embedding was persisted
	var count int
	err = warehouseDB.QueryRow(`
		SELECT COUNT(*) FROM embeddings
		WHERE entity_type = ? AND entity_id = ?
	`, "conversation", 1).Scan(&count)
	if err != nil {
		t.Fatal(err)
	}

	if count != 1 {
		t.Errorf("expected 1 embedding, got %d", count)
	}

	// Verify embedding data
	var embeddingBlob []byte
	var dimension int
	err = warehouseDB.QueryRow(`
		SELECT embedding_blob, dimension FROM embeddings
		WHERE entity_type = ? AND entity_id = ?
	`, "conversation", 1).Scan(&embeddingBlob, &dimension)
	if err != nil {
		t.Fatal(err)
	}

	if dimension != 5 {
		t.Errorf("expected dimension 5, got %d", dimension)
	}

	// Decode embedding blob
	embedding, err := blobToFloat64Slice(embeddingBlob)
	if err != nil {
		t.Fatalf("failed to decode embedding: %v", err)
	}

	if len(embedding) != 5 {
		t.Errorf("expected 5 values, got %d", len(embedding))
	}

	// Check values
	expected := []float64{0.1, 0.2, 0.3, 0.4, 0.5}
	for i, v := range expected {
		if embedding[i] != v {
			t.Errorf("embedding[%d] = %f, want %f", i, embedding[i], v)
		}
	}
}

func TestEmbeddingJob_Idempotency(t *testing.T) {
	// Create fake Gemini server
	var callCount int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		callCount++
		resp := gemini.EmbedContentResponse{
			Embedding: &gemini.Embedding{
				Values: []float64{0.1, 0.2, 0.3},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	// Create temp warehouse DB
	tmpfile, err := os.CreateTemp("", "eve-warehouse-*.db")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(tmpfile.Name())
	tmpfile.Close()

	dbPath := tmpfile.Name()

	// Run migrations
	if err := migrate.MigrateWarehouse(dbPath); err != nil {
		t.Fatalf("migration failed: %v", err)
	}

	warehouseDB, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		t.Fatal(err)
	}
	defer warehouseDB.Close()

	// Insert test data
	insertTestConversationForEmbedding(t, warehouseDB)

	// Create Gemini client with fake server
	geminiClient := gemini.NewClient("fake-key")
	geminiClient.HttpClient.Transport = &embedTestTransport{
		testServer: server,
	}

	// Create embedding job handler
	handler := NewEmbeddingJobHandler(warehouseDB, geminiClient, "gemini-embedding-001")

	// Create job
	payload := EmbeddingJobPayload{
		EntityType: "conversation",
		EntityID:   1,
	}
	payloadJSON, _ := json.Marshal(payload)
	job := &queue.Job{
		ID:          "job-1",
		Type:        "embedding",
		PayloadJSON: string(payloadJSON),
	}

	// Execute job twice
	ctx := context.Background()
	err = handler(ctx, job)
	if err != nil {
		t.Fatalf("first execution failed: %v", err)
	}

	err = handler(ctx, job)
	if err != nil {
		t.Fatalf("second execution failed: %v", err)
	}

	// Verify only one embedding exists (upsert behavior)
	var count int
	err = warehouseDB.QueryRow(`
		SELECT COUNT(*) FROM embeddings
		WHERE entity_type = ? AND entity_id = ?
	`, "conversation", 1).Scan(&count)
	if err != nil {
		t.Fatal(err)
	}

	if count != 1 {
		t.Errorf("expected 1 embedding after two runs, got %d", count)
	}

	// Both calls should have happened (we don't dedupe at handler level)
	if callCount != 2 {
		t.Errorf("expected 2 API calls, got %d", callCount)
	}
}

func TestEmbeddingJob_Facets(t *testing.T) {
	// Create fake Gemini server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := gemini.EmbedContentResponse{
			Embedding: &gemini.Embedding{
				Values: []float64{0.1, 0.2, 0.3},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	// Create temp warehouse DB
	tmpfile, err := os.CreateTemp("", "eve-warehouse-*.db")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(tmpfile.Name())
	tmpfile.Close()

	dbPath := tmpfile.Name()

	// Run migrations
	if err := migrate.MigrateWarehouse(dbPath); err != nil {
		t.Fatalf("migration failed: %v", err)
	}

	warehouseDB, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		t.Fatal(err)
	}
	defer warehouseDB.Close()

	// Insert test data (includes facet rows)
	insertTestConversationForEmbedding(t, warehouseDB)

	// Create Gemini client with fake server
	geminiClient := gemini.NewClient("fake-key")
	geminiClient.HttpClient.Transport = &embedTestTransport{testServer: server}

	handler := NewEmbeddingJobHandler(warehouseDB, geminiClient, "gemini-embedding-001")

	tests := []struct {
		entityType string
		entityID   int
	}{
		{entityType: "entity", entityID: 1},
		{entityType: "topic", entityID: 1},
		{entityType: "emotion", entityID: 1},
		{entityType: "humor_item", entityID: 1},
	}

	for _, tt := range tests {
		t.Run(tt.entityType, func(t *testing.T) {
			payload := EmbeddingJobPayload{EntityType: tt.entityType, EntityID: tt.entityID}
			payloadJSON, _ := json.Marshal(payload)
			job := &queue.Job{ID: "job-" + tt.entityType, Type: "embedding", PayloadJSON: string(payloadJSON)}
			if err := handler(context.Background(), job); err != nil {
				t.Fatalf("job failed: %v", err)
			}
			var count int
			if err := warehouseDB.QueryRow(
				`SELECT COUNT(*) FROM embeddings WHERE entity_type = ? AND entity_id = ?`,
				tt.entityType, tt.entityID,
			).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if count != 1 {
				t.Fatalf("expected 1 embedding row, got %d", count)
			}
		})
	}
}

func TestFloat64SliceToBlob(t *testing.T) {
	values := []float64{1.5, 2.7, 3.9, 4.1}

	// Encode
	blob, err := float64SliceToBlob(values)
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}

	// Decode
	decoded, err := blobToFloat64Slice(blob)
	if err != nil {
		t.Fatalf("decode failed: %v", err)
	}

	// Compare
	if len(decoded) != len(values) {
		t.Errorf("length mismatch: got %d, want %d", len(decoded), len(values))
	}

	for i, v := range values {
		if decoded[i] != v {
			t.Errorf("value[%d] = %f, want %f", i, decoded[i], v)
		}
	}
}

// insertTestConversationForEmbedding inserts a test conversation with messages
func insertTestConversationForEmbedding(t *testing.T, db *sql.DB) {
	// Insert contact
	_, err := db.Exec(`
		INSERT INTO contacts (id, name, is_me)
		VALUES (1, 'Alice', 0), (2, 'Me', 1)
	`)
	if err != nil {
		t.Fatal(err)
	}

	// Insert chat
	_, err = db.Exec(`
		INSERT INTO chats (id, chat_identifier, chat_name)
		VALUES (1, 'chat-1', 'Test Chat')
	`)
	if err != nil {
		t.Fatal(err)
	}

	// Insert conversation
	now := time.Now()
	_, err = db.Exec(`
		INSERT INTO conversations (id, chat_id, start_time, end_time, message_count)
		VALUES (1, 1, ?, ?, 2)
	`, now.Add(-10*time.Minute), now)
	if err != nil {
		t.Fatal(err)
	}

	// Insert messages
	_, err = db.Exec(`
		INSERT INTO messages (id, chat_id, sender_id, content, timestamp, is_from_me, guid, conversation_id)
		VALUES
			(1, 1, 1, 'Hello', ?, 0, 'msg-1', 1),
			(2, 1, 2, 'Hi there', ?, 1, 'msg-2', 1)
	`, now.Add(-10*time.Minute), now.Add(-5*time.Minute))
	if err != nil {
		t.Fatal(err)
	}

	// Insert facet rows for embedding entity types (entity/topic/emotion/humor_item)
	_, err = db.Exec(`
		INSERT INTO entities (id, conversation_id, chat_id, contact_id, title) VALUES (1, 1, 1, 1, 'Pizza');
		INSERT INTO topics (id, conversation_id, chat_id, contact_id, title) VALUES (1, 1, 1, 1, 'Lunch');
		INSERT INTO emotions (id, conversation_id, chat_id, contact_id, emotion_type) VALUES (1, 1, 1, 1, 'Happy');
		INSERT INTO humor_items (id, conversation_id, chat_id, contact_id, snippet) VALUES (1, 1, 1, 1, 'lol');
	`)
	if err != nil {
		t.Fatal(err)
	}
}

// embedTestTransport redirects requests to test server (avoiding conflict with analysis_job_test.go)
type embedTestTransport struct {
	testServer *httptest.Server
}

func (t *embedTestTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Redirect all requests to test server
	req.URL.Scheme = "http"
	req.URL.Host = strings.TrimPrefix(t.testServer.URL, "http://")
	return t.testServer.Client().Transport.RoundTrip(req)
}
