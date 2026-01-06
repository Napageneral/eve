---
id: enneagram-v1
name: Enneagram Analysis
version: 1.0.0
category: fun
tags: [personality, enneagram, group-analysis]

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
  result_title: "{{chat_title}} : Enneagram Analysis"
  model_preferences: [claude-sonnet-4]
---

# Enneagram Analysis

Based on the provided conversation analysis data, determine the Enneagram type and wing that best fits each participant's chat behavior and communication patterns.

## Output Format

For each person:

**Type & Wing:** [Type Number] with [Wing] (e.g., "Type 7 with wing 6" or "7w6")

**What This Means:**
- Brief 1-2 sentence explanation of the type
- Key motivations and fears for this type

**Evidence from Chats:**
- 2-4 specific communication patterns that support this typing
- Reference message IDs when particularly clear examples exist
- Note any behavioral patterns (conflict style, decision-making, humor, etc.)

**Growth Tips:**
- 1-2 practical suggestions for this type in communication

## Enneagram Quick Reference

1. **The Reformer** — Principled, purposeful, perfectionistic
2. **The Helper** — Generous, demonstrative, people-pleasing
3. **The Achiever** — Success-oriented, pragmatic, adaptive
4. **The Individualist** — Expressive, dramatic, self-absorbed
5. **The Investigator** — Perceptive, innovative, secretive
6. **The Loyalist** — Engaging, responsible, anxious
7. **The Enthusiast** — Spontaneous, versatile, scattered
8. **The Challenger** — Self-confident, decisive, confrontational
9. **The Peacemaker** — Receptive, reassuring, complacent

## Rules

- Use ONLY provided analyses—no invention
- Be honest if evidence is unclear
- Keep tone warm and non-judgmental
- This is for self-understanding, not criticism

