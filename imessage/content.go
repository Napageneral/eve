package imessage

import (
	"strings"
	"unicode"
)

// NormalizePhoneNumber normalizes phone numbers for consistent matching
// - Removes all non-digit chars
// - If 11 digits starting with 1, drops the leading 1 (US numbers)
func NormalizePhoneNumber(phone string) string {
	// Remove all non-digit characters
	var b strings.Builder
	b.Grow(len(phone))
	for _, r := range phone {
		if r >= '0' && r <= '9' {
			b.WriteRune(r)
		}
	}
	digits := b.String()

	// If it's a US number (11 digits starting with 1), remove the leading 1
	if len(digits) == 11 && strings.HasPrefix(digits, "1") {
		return digits[1:]
	}
	return digits
}

// NormalizeIdentifier normalizes a phone/email identifier and returns its type
func NormalizeIdentifier(identifier string) (normalized string, typ string) {
	id := strings.TrimSpace(identifier)
	if id == "" {
		return "", "phone"
	}
	if strings.Contains(id, "@") {
		return strings.ToLower(id), "email"
	}
	return NormalizePhoneNumber(id), "phone"
}

// NormalizePhoneE164 normalizes phone numbers to E.164-ish format
// Used for the Comms identities table
func NormalizePhoneE164(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	// Keep + and digits only
	var b strings.Builder
	for _, r := range s {
		if (r >= '0' && r <= '9') || r == '+' {
			b.WriteRune(r)
		}
	}
	out := b.String()

	// Best-effort US normalization:
	// - 10 digits -> +1XXXXXXXXXX
	// - 11 digits starting with 1 -> +1XXXXXXXXXX
	// - already has + -> leave
	if strings.HasPrefix(out, "+") {
		return out
	}
	digits := out
	if len(digits) == 10 {
		return "+1" + digits
	}
	if len(digits) == 11 && strings.HasPrefix(digits, "1") {
		return "+" + digits
	}
	// For international numbers without +, just prepend +
	if len(digits) > 10 {
		return "+" + digits
	}
	return out
}

// DecodeAttributedBody extracts text from NSAttributedString blob
// This is a pragmatic extraction, not a full decoder
func DecodeAttributedBody(attributedBody []byte) string {
	if len(attributedBody) == 0 {
		return ""
	}

	s := string(attributedBody)

	if !strings.Contains(s, "NSNumber") {
		return ""
	}

	// Take everything before NSNumber
	if idx := strings.Index(s, "NSNumber"); idx >= 0 {
		s = s[:idx]
	}

	// Take everything after NSString
	if !strings.Contains(s, "NSString") {
		return ""
	}
	parts := strings.SplitN(s, "NSString", 2)
	if len(parts) != 2 {
		return ""
	}
	s = parts[1]

	// Take everything before NSDictionary
	if !strings.Contains(s, "NSDictionary") {
		return ""
	}
	parts = strings.SplitN(s, "NSDictionary", 2)
	if len(parts) != 2 {
		return ""
	}
	s = parts[0]

	// Slice [6:-12] and strip
	runes := []rune(s)
	if len(runes) < 6+12 {
		return strings.TrimSpace(s)
	}
	s = string(runes[6 : len(runes)-12])
	return strings.TrimSpace(s)
}

// CleanMessageContent cleans message content for storage
func CleanMessageContent(content string) string {
	if content == "" {
		return ""
	}

	// Keep printable chars plus whitespace
	var b strings.Builder
	b.Grow(len(content))
	for _, r := range content {
		if unicode.IsPrint(r) || r == ' ' || r == '\n' || r == '\t' {
			b.WriteRune(r)
		}
	}
	cleaned := b.String()

	// Remove problematic characters
	cleaned = strings.ReplaceAll(cleaned, "\uFFFC", "") // object replacement char
	cleaned = strings.ReplaceAll(cleaned, "\x01", "")
	cleaned = strings.ReplaceAll(cleaned, "\uFFFD", "") // replacement char

	// Trim space and null bytes
	cleaned = strings.TrimSpace(cleaned)
	cleaned = strings.Trim(cleaned, "\x00")
	return cleaned
}

// DeriveMediaType determines the media_type category from mime_type
func DeriveMediaType(mimeType string, isSticker bool) string {
	if isSticker {
		return "sticker"
	}

	mimeType = strings.ToLower(mimeType)

	if strings.HasPrefix(mimeType, "image/") {
		return "image"
	}
	if strings.HasPrefix(mimeType, "video/") {
		return "video"
	}
	if strings.HasPrefix(mimeType, "audio/") {
		return "audio"
	}
	if strings.HasPrefix(mimeType, "application/pdf") ||
		strings.HasPrefix(mimeType, "application/msword") ||
		strings.HasPrefix(mimeType, "application/vnd.openxmlformats-officedocument") ||
		strings.HasPrefix(mimeType, "application/vnd.ms-") ||
		strings.HasPrefix(mimeType, "text/") {
		return "document"
	}

	return "document"
}
