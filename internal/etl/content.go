package etl

import (
	"bytes"
	"strings"
	"unicode"
)

// normalizePhoneNumber mirrors ChatStats' normalize_phone_number():
// - remove all non-digit chars
// - if 11 digits starting with 1, drop the leading 1 (US numbers)
func normalizePhoneNumber(phone string) string {
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

func normalizeIdentifier(identifier string) (normalized string, typ string) {
	id := strings.TrimSpace(identifier)
	if id == "" {
		return "", "phone"
	}
	if strings.Contains(id, "@") {
		return strings.ToLower(id), "email"
	}
	return normalizePhoneNumber(id), "phone"
}

// decodeAttributedBody mirrors the (admittedly hacky) ChatStats decode_attributed_body()
// behavior, which is optimized for the common macOS Messages typedstream payloads.
//
// This is intentionally not a full NSAttributedString decoder; it is a pragmatic
// extraction of the embedded NSString content that ChatStats relied on.
func decodeAttributedBody(attributedBody []byte) string {
	if len(attributedBody) == 0 {
		return ""
	}

	// NOTE: Python used .decode('utf-8', errors='surrogateescape') and then searched
	// for ASCII markers. In Go, treating bytes as string preserves raw bytes for
	// substring search of ASCII markers.
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

	// ChatStats slices [6:-12] and then .strip()
	runes := []rune(s)
	if len(runes) < 6+12 {
		return strings.TrimSpace(s)
	}
	s = string(runes[6 : len(runes)-12])
	return strings.TrimSpace(s)
}

// cleanMessageContent mirrors ChatStats _clean_message_content().
func cleanMessageContent(content string) string {
	if content == "" {
		return ""
	}

	// Keep printable chars plus whitespace.
	var b strings.Builder
	b.Grow(len(content))
	for _, r := range content {
		if unicode.IsPrint(r) || r == ' ' || r == '\n' || r == '\t' {
			b.WriteRune(r)
		}
	}
	cleaned := b.String()

	// Remove problematic characters
	// - U+FFFC object replacement char
	// - \x01
	// - U+FFFD replacement char
	cleaned = strings.ReplaceAll(cleaned, "\uFFFC", "")
	cleaned = strings.ReplaceAll(cleaned, "\x01", "")
	cleaned = strings.ReplaceAll(cleaned, "\uFFFD", "")

	// Trim space and also trim stray null bytes if any made it through
	cleaned = strings.TrimSpace(cleaned)
	cleaned = string(bytes.Trim(cleanedAsBytes(cleaned), "\x00"))
	return cleaned
}

func cleanedAsBytes(s string) []byte {
	// Helper to avoid allocations in the common case.
	if s == "" {
		return nil
	}
	return []byte(s)
}

