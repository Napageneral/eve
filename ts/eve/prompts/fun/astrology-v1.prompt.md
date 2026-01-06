---
id: astrology-v1
name: Astrological Signs
version: 1.0.0
category: fun
tags: [personality, astrology, zodiac, group-analysis]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: analyses-year-personality
  alternatives: [analyses-month-recent]

always_on: [artifact-rules-min, privacy-redlines, app-meta]

vars:
  chat_title:
    type: string
    required: false

execution:
  mode: chatbot-streaming
  result_type: document
  result_title: "{{chat_title}} : Astrological Analysis"
  model_preferences: [claude-sonnet-4]
---

# Astrological Signs Analysis

Based on the provided conversation analysis data, determine which zodiac sign (Sun, Moon, and Rising) best matches each participant's communication patterns and behavioral tendencies in chat.

## Output Format

For each person:

**Sun Sign:** [Zodiac Sign]
- Core personality traits visible in chat
- Primary motivation and energy
- Key evidence from message patterns

**Moon Sign:** [Zodiac Sign]  
- Emotional expression and reactions
- How they handle conflicts or stress
- Evidence from sentiment/tone analysis

**Rising Sign:** [Zodiac Sign]
- First impression and social presence
- How they initiate conversations
- Their "public face" in the group

**Supporting Evidence:**
- 3-5 specific communication patterns that support these signs
- Reference message IDs for clear examples
- Note any particularly strong alignments or interesting contradictions

**Brief Compatibility Note:**
- How their signs interact with other participants
- Any notable astrological dynamics in the group

## Zodiac Quick Reference

**Fire Signs (Aries, Leo, Sagittarius):** Passionate, enthusiastic, direct, initiators

**Earth Signs (Taurus, Virgo, Capricorn):** Practical, grounded, reliable, detail-oriented

**Air Signs (Gemini, Libra, Aquarius):** Social, intellectual, communicative, idea-focused

**Water Signs (Cancer, Scorpio, Pisces):** Emotional, intuitive, empathetic, depth-seeking

## Rules

- Use ONLY provided analyses—no invention
- This is for fun—keep tone playful and warm
- Be honest if evidence is unclear ("Could be Gemini or Libra based on...")
- Avoid stereotypes—focus on real patterns
- Multiple people can share signs
- Ground every assignment in observable chat behavior

## Style

- Write with astrological flair but grounded in data
- Make it entertaining and insightful
- Avoid overly serious predictions—focus on personality insights
- Keep each person's section concise (4-6 bullets total)

