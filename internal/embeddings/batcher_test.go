package embeddings

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/tylerchilds/eve/internal/gemini"
)

func TestBatcher_SizeBasedFlush(t *testing.T) {
	// Track number of batch requests
	var requestCount int32

	// Create fake Gemini server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)

		// Parse request to get batch size
		var req gemini.BatchEmbedContentsRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("failed to decode request: %v", err)
			return
		}

		// Return fake embeddings
		embeddings := make([]gemini.Embedding, len(req.Requests))
		for i := range embeddings {
			embeddings[i] = gemini.Embedding{
				Values: []float64{1.0, 2.0, 3.0},
			}
		}

		resp := gemini.BatchEmbedContentsResponse{
			Embeddings: embeddings,
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	// Create Gemini client with fake server
	client := gemini.NewClient("fake-key")
	client.HttpClient.Transport = &redirectTransport{target: server.URL}

	// Create batcher with small batch size
	batcher := NewBatcher(client, "text-embedding-005")
	batcher.maxBatchSize = 10

	// Add 25 tasks (should trigger 3 flushes: 10, 10, 5)
	for i := 0; i < 25; i++ {
		batcher.Add(EmbeddingTask{
			EntityType: "conversation",
			EntityID:   i + 1,
			Text:       "test text",
		})
	}

	// Flush remaining
	batcher.Flush()

	// Wait for results
	results := make([]EmbeddingResult, 0)
	timeout := time.After(2 * time.Second)
	for len(results) < 25 {
		select {
		case result := <-batcher.Results():
			results = append(results, result)
		case <-timeout:
			t.Fatalf("timeout waiting for results, got %d/25", len(results))
		}
	}

	batcher.Close()

	// Check that we got all results
	if len(results) != 25 {
		t.Errorf("expected 25 results, got %d", len(results))
	}

	// Check no errors
	for _, result := range results {
		if result.Error != nil {
			t.Errorf("unexpected error: %v", result.Error)
		}
		if len(result.Embedding) != 3 {
			t.Errorf("expected embedding length 3, got %d", len(result.Embedding))
		}
	}

	// Check that we made 3 batch requests
	count := atomic.LoadInt32(&requestCount)
	if count != 3 {
		t.Errorf("expected 3 batch requests, got %d", count)
	}
}

func TestBatcher_TimeBasedFlush(t *testing.T) {
	// Track number of batch requests
	var requestCount int32

	// Create fake Gemini server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)

		// Parse request
		var req gemini.BatchEmbedContentsRequest
		json.NewDecoder(r.Body).Decode(&req)

		// Return fake embeddings
		embeddings := make([]gemini.Embedding, len(req.Requests))
		for i := range embeddings {
			embeddings[i] = gemini.Embedding{
				Values: []float64{1.0, 2.0, 3.0},
			}
		}

		resp := gemini.BatchEmbedContentsResponse{
			Embeddings: embeddings,
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	// Create Gemini client with fake server
	client := gemini.NewClient("fake-key")
	client.HttpClient.Transport = &redirectTransport{target: server.URL}

	// Create batcher with short flush interval
	batcher := NewBatcher(client, "text-embedding-005")
	batcher.flushInterval = 100 * time.Millisecond

	// Add 5 tasks (below batch size)
	for i := 0; i < 5; i++ {
		batcher.Add(EmbeddingTask{
			EntityType: "conversation",
			EntityID:   i + 1,
			Text:       "test text",
		})
	}

	// Wait for timer flush (should happen within 200ms)
	time.Sleep(200 * time.Millisecond)

	// Collect results
	results := make([]EmbeddingResult, 0)
	timeout := time.After(1 * time.Second)
	for len(results) < 5 {
		select {
		case result := <-batcher.Results():
			results = append(results, result)
		case <-timeout:
			t.Fatalf("timeout waiting for results, got %d/5", len(results))
		}
	}

	batcher.Close()

	// Check that we got all results
	if len(results) != 5 {
		t.Errorf("expected 5 results, got %d", len(results))
	}

	// Check that we made at least 1 batch request
	count := atomic.LoadInt32(&requestCount)
	if count < 1 {
		t.Errorf("expected at least 1 batch request, got %d", count)
	}
}

func TestBatcher_EmptyBatch(t *testing.T) {
	// Create fake Gemini server (should not be called)
	var requestCount int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)
		t.Error("unexpected request to server")
	}))
	defer server.Close()

	// Create Gemini client with fake server
	client := gemini.NewClient("fake-key")
	client.HttpClient.Transport = &redirectTransport{target: server.URL}

	// Create batcher
	batcher := NewBatcher(client, "text-embedding-005")

	// Flush empty batch
	batcher.Flush()
	batcher.Close()

	// Check that no requests were made
	count := atomic.LoadInt32(&requestCount)
	if count != 0 {
		t.Errorf("expected 0 batch requests, got %d", count)
	}
}

// redirectTransport redirects all requests to a target URL
type redirectTransport struct {
	target string
}

func (t *redirectTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Change request URL to target
	req.URL.Scheme = "http"
	req.URL.Host = req.URL.Host
	if len(t.target) > 0 {
		req.URL, _ = req.URL.Parse(t.target)
	}
	return http.DefaultTransport.RoundTrip(req)
}
