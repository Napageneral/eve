package gemini

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"time"

	"github.com/brandtty/eve/internal/ratelimit"
)

const (
	baseURL             = "https://generativelanguage.googleapis.com/v1beta"
	maxRetries          = 5
	initialBackoff      = 500 * time.Millisecond
	maxBackoff          = 30 * time.Second
	defaultTimeout      = 60 * time.Second
	maxIdleConns        = 1000
	maxConnsPerHost     = 1000
	idleConnTimeout     = 90 * time.Second
	tlsHandshakeTimeout = 10 * time.Second
)

// Client is a Gemini API client with HTTP/2 support and retries
type Client struct {
	HttpClient      *http.Client // Exported for testing
	apiKey          string
	analysisLimiter *ratelimit.LeakyBucket
	embedLimiter    *ratelimit.LeakyBucket
}

// NewClient creates a new Gemini client with HTTP/2 pooling and retries
func NewClient(apiKey string) *Client {
	transport := &http.Transport{
		MaxIdleConns:        maxIdleConns,
		MaxIdleConnsPerHost: maxConnsPerHost,
		MaxConnsPerHost:     maxConnsPerHost,
		IdleConnTimeout:     idleConnTimeout,
		TLSHandshakeTimeout: tlsHandshakeTimeout,
		ForceAttemptHTTP2:   true, // Enable HTTP/2
	}

	HttpClient := &http.Client{
		Transport: transport,
		Timeout:   defaultTimeout,
	}

	return &Client{
		HttpClient: HttpClient,
		apiKey:     apiKey,
	}
}

// SetAnalysisRPM sets a smooth rate limit for GenerateContent requests.
// rpm<=0 disables rate limiting.
func (c *Client) SetAnalysisRPM(rpm int) {
	if c == nil {
		return
	}
	if rpm <= 0 {
		if c.analysisLimiter != nil {
			c.analysisLimiter.Close()
		}
		c.analysisLimiter = nil
		return
	}
	if c.analysisLimiter == nil {
		c.analysisLimiter = ratelimit.NewLeakyBucketFromRPM(rpm)
		return
	}
	c.analysisLimiter.SetRPM(rpm)
}

// SetEmbedRPM sets a smooth rate limit for EmbedContent requests.
// rpm<=0 disables rate limiting.
func (c *Client) SetEmbedRPM(rpm int) {
	if c == nil {
		return
	}
	if rpm <= 0 {
		if c.embedLimiter != nil {
			c.embedLimiter.Close()
		}
		c.embedLimiter = nil
		return
	}
	if c.embedLimiter == nil {
		c.embedLimiter = ratelimit.NewLeakyBucketFromRPM(rpm)
		return
	}
	c.embedLimiter.SetRPM(rpm)
}

// GenerateContentRequest represents the request for generateContent API
type GenerateContentRequest struct {
	Contents         []Content         `json:"contents"`
	GenerationConfig *GenerationConfig `json:"generationConfig,omitempty"`
	SafetySettings   []SafetySetting   `json:"safetySettings,omitempty"`
}

// GenerationConfig configures generation behavior.
// See: https://ai.google.dev/gemini-api/docs/gemini-3
type GenerationConfig struct {
	ThinkingConfig   *ThinkingConfig `json:"thinkingConfig,omitempty"`
	ResponseMimeType string          `json:"responseMimeType,omitempty"`
	ResponseSchema   any             `json:"responseSchema,omitempty"`
}

// ThinkingConfig configures Gemini 3 thinking.
type ThinkingConfig struct {
	ThinkingLevel string `json:"thinkingLevel,omitempty"` // minimal|low|medium|high (varies by model)
}

// SafetySetting configures per-category safety thresholds.
type SafetySetting struct {
	Category  string `json:"category"`
	Threshold string `json:"threshold"`
}

// Content represents a message in the conversation
type Content struct {
	Role  string `json:"role,omitempty"`
	Parts []Part `json:"parts"`
}

// Part represents a content part (text, inline data, etc.)
type Part struct {
	Text string `json:"text,omitempty"`
}

// GenerateContentResponse represents the response from generateContent API
type GenerateContentResponse struct {
	Candidates     []Candidate     `json:"candidates,omitempty"`
	PromptFeedback *PromptFeedback `json:"promptFeedback,omitempty"`
	Error          *APIError       `json:"error,omitempty"`
}

type SafetyRating struct {
	Category    string `json:"category"`
	Probability string `json:"probability"`
}

type PromptFeedback struct {
	BlockReason        string         `json:"blockReason,omitempty"`
	BlockReasonMessage string         `json:"blockReasonMessage,omitempty"`
	SafetyRatings      []SafetyRating `json:"safetyRatings,omitempty"`
}

// Candidate represents a generated response candidate
type Candidate struct {
	Content       Content        `json:"content"`
	FinishReason  string         `json:"finishReason,omitempty"`
	SafetyRatings []SafetyRating `json:"safetyRatings,omitempty"`
}

// EmbedContentRequest represents the request for embedContent API
type EmbedContentRequest struct {
	Model   string  `json:"model"`
	Content Content `json:"content"`
}

// EmbedContentResponse represents the response from embedContent API
type EmbedContentResponse struct {
	Embedding *Embedding `json:"embedding,omitempty"`
	Error     *APIError  `json:"error,omitempty"`
}

// BatchEmbedContentsRequest represents the request for batchEmbedContents API
type BatchEmbedContentsRequest struct {
	Requests []EmbedContentRequest `json:"requests"`
}

// BatchEmbedContentsResponse represents the response from batchEmbedContents API
type BatchEmbedContentsResponse struct {
	Embeddings []Embedding `json:"embeddings,omitempty"`
	Error      *APIError   `json:"error,omitempty"`
}

// Embedding represents an embedding vector
type Embedding struct {
	Values []float64 `json:"values"`
}

// APIError represents an error from the Gemini API
type APIError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Status  string `json:"status"`
}

// Error implements the error interface for APIError
func (e *APIError) Error() string {
	return fmt.Sprintf("gemini API error %d (%s): %s", e.Code, e.Status, e.Message)
}

// GenerateContent calls the Gemini generateContent API for analysis
// Returns the response or an error with retry logic
func (c *Client) GenerateContent(model string, req *GenerateContentRequest) (*GenerateContentResponse, error) {
	return c.GenerateContentWithContext(context.Background(), model, req)
}

func (c *Client) GenerateContentWithContext(ctx context.Context, model string, req *GenerateContentRequest) (*GenerateContentResponse, error) {
	url := fmt.Sprintf("%s/models/%s:generateContent?key=%s", baseURL, model, c.apiKey)

	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt <= maxRetries; attempt++ {
		if c.analysisLimiter != nil {
			if err := c.analysisLimiter.Wait(ctx); err != nil {
				return nil, err
			}
		}
		if attempt > 0 {
			// Calculate exponential backoff with jitter
			backoff := calculateBackoff(attempt)
			time.Sleep(backoff)
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("failed to create request: %w", err)
		}

		httpReq.Header.Set("Content-Type", "application/json")

		resp, err := c.HttpClient.Do(httpReq)
		if err != nil {
			lastErr = fmt.Errorf("request failed: %w", err)
			if isRetryable(err, 0) {
				continue
			}
			return nil, lastErr
		}

		respBody, err := io.ReadAll(resp.Body)
		resp.Body.Close()

		if err != nil {
			lastErr = fmt.Errorf("failed to read response: %w", err)
			continue
		}

		// Check for retryable HTTP status codes
		if isRetryableStatus(resp.StatusCode) {
			lastErr = fmt.Errorf("retryable status code %d", resp.StatusCode)
			continue
		}

		// Parse response
		var result GenerateContentResponse
		if err := json.Unmarshal(respBody, &result); err != nil {
			return nil, fmt.Errorf("failed to unmarshal response: %w", err)
		}

		// Check for API error
		if result.Error != nil {
			if isRetryableStatus(result.Error.Code) {
				lastErr = result.Error
				continue
			}
			return nil, result.Error
		}

		// Success
		return &result, nil
	}

	return nil, fmt.Errorf("max retries exceeded: %w", lastErr)
}

// EmbedContent calls the Gemini embedContent API for embeddings
// Returns the response or an error with retry logic
func (c *Client) EmbedContent(req *EmbedContentRequest) (*EmbedContentResponse, error) {
	return c.EmbedContentWithContext(context.Background(), req)
}

func (c *Client) EmbedContentWithContext(ctx context.Context, req *EmbedContentRequest) (*EmbedContentResponse, error) {
	url := fmt.Sprintf("%s/models/%s:embedContent?key=%s", baseURL, req.Model, c.apiKey)

	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt <= maxRetries; attempt++ {
		if c.embedLimiter != nil {
			if err := c.embedLimiter.Wait(ctx); err != nil {
				return nil, err
			}
		}
		if attempt > 0 {
			// Calculate exponential backoff with jitter
			backoff := calculateBackoff(attempt)
			time.Sleep(backoff)
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("failed to create request: %w", err)
		}

		httpReq.Header.Set("Content-Type", "application/json")

		resp, err := c.HttpClient.Do(httpReq)
		if err != nil {
			lastErr = fmt.Errorf("request failed: %w", err)
			if isRetryable(err, 0) {
				continue
			}
			return nil, lastErr
		}

		respBody, err := io.ReadAll(resp.Body)
		resp.Body.Close()

		if err != nil {
			lastErr = fmt.Errorf("failed to read response: %w", err)
			continue
		}

		// Check for retryable HTTP status codes
		if isRetryableStatus(resp.StatusCode) {
			lastErr = fmt.Errorf("retryable status code %d", resp.StatusCode)
			continue
		}

		// Parse response
		var result EmbedContentResponse
		if err := json.Unmarshal(respBody, &result); err != nil {
			return nil, fmt.Errorf("failed to unmarshal response: %w", err)
		}

		// Check for API error
		if result.Error != nil {
			if isRetryableStatus(result.Error.Code) {
				lastErr = result.Error
				continue
			}
			return nil, result.Error
		}

		// Success
		return &result, nil
	}

	return nil, fmt.Errorf("max retries exceeded: %w", lastErr)
}

// BatchEmbedContents calls the Gemini batchEmbedContents API for batch embeddings
// Returns the response or an error with retry logic
func (c *Client) BatchEmbedContents(model string, requests []EmbedContentRequest) (*BatchEmbedContentsResponse, error) {
	return c.BatchEmbedContentsWithContext(context.Background(), model, requests)
}

func (c *Client) BatchEmbedContentsWithContext(ctx context.Context, model string, requests []EmbedContentRequest) (*BatchEmbedContentsResponse, error) {
	url := fmt.Sprintf("%s/models/%s:batchEmbedContents?key=%s", baseURL, model, c.apiKey)

	// Set model in each request
	for i := range requests {
		requests[i].Model = model
	}

	batchReq := BatchEmbedContentsRequest{
		Requests: requests,
	}

	body, err := json.Marshal(batchReq)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt <= maxRetries; attempt++ {
		if c.embedLimiter != nil {
			if err := c.embedLimiter.Wait(ctx); err != nil {
				return nil, err
			}
		}
		if attempt > 0 {
			// Calculate exponential backoff with jitter
			backoff := calculateBackoff(attempt)
			time.Sleep(backoff)
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("failed to create request: %w", err)
		}

		httpReq.Header.Set("Content-Type", "application/json")

		resp, err := c.HttpClient.Do(httpReq)
		if err != nil {
			lastErr = fmt.Errorf("request failed: %w", err)
			if isRetryable(err, 0) {
				continue
			}
			return nil, lastErr
		}

		respBody, err := io.ReadAll(resp.Body)
		resp.Body.Close()

		if err != nil {
			lastErr = fmt.Errorf("failed to read response: %w", err)
			continue
		}

		// Check for retryable HTTP status codes
		if isRetryableStatus(resp.StatusCode) {
			lastErr = fmt.Errorf("retryable status code %d", resp.StatusCode)
			continue
		}

		// Parse response
		var result BatchEmbedContentsResponse
		if err := json.Unmarshal(respBody, &result); err != nil {
			return nil, fmt.Errorf("failed to unmarshal response: %w", err)
		}

		// Check for API error
		if result.Error != nil {
			if isRetryableStatus(result.Error.Code) {
				lastErr = result.Error
				continue
			}
			return nil, result.Error
		}

		// Success
		return &result, nil
	}

	return nil, fmt.Errorf("max retries exceeded: %w", lastErr)
}

// isRetryableStatus checks if an HTTP status code is retryable
func isRetryableStatus(statusCode int) bool {
	return statusCode == http.StatusTooManyRequests || // 429
		statusCode >= 500 // 5xx server errors
}

// isRetryable checks if an error is retryable (network errors, etc.)
func isRetryable(err error, statusCode int) bool {
	// Network errors are retryable
	return true
}

// calculateBackoff calculates exponential backoff with jitter
func calculateBackoff(attempt int) time.Duration {
	// Exponential backoff: initialBackoff * 2^attempt
	backoff := float64(initialBackoff) * math.Pow(2, float64(attempt-1))

	// Cap at maxBackoff
	if backoff > float64(maxBackoff) {
		backoff = float64(maxBackoff)
	}

	// Add jitter (±25%)
	jitter := backoff * 0.25 * (rand.Float64()*2 - 1)
	backoff += jitter

	return time.Duration(backoff)
}
