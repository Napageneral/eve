---
id: gift-ideas-v1
name: Gift Ideas
version: 1.0.0
category: fun
tags: [practical, gifts, relationships]

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
  result_title: "{{chat_title}} : Gift Ideas"
  model_preferences: [claude-sonnet-4]
---

# Gift Ideas

Based on the provided conversation analysis data, suggest specific, thoughtful gift ideas for each participant.

## Output Format

For each person:

**Gift Ideas (Ranked):**
1. **[Specific Gift Name]** — Est. Cost: $X–Y
   - Why: Connection to their interests/preferences from chats
   - Where: Brief sourcing suggestion if helpful
   
2. **[Specific Gift Name]** — Est. Cost: $X–Y
   - Why: ...

*(Provide 2-4 ideas per person, ranked by likely appreciation)*

**Supporting Evidence:**
- Quote or cite specific topics, hobbies, or expressed desires from chat logs
- Reference message IDs for particularly clear examples
- Note patterns: "Frequently mentions photography [#m1234, #m5678]"

## Guidelines

- **Be specific:** "Leica Q3 compact camera" not "a camera"
- **Range costs:** Budget-friendly to splurge options
- **Ground in evidence:** Every gift should clearly connect to something they've discussed
- **Practical + Thoughtful:** Mix useful items with emotional/meaningful gifts
- **Respect context:** Consider relationship dynamics, occasions mentioned, life events

## Rules

- Use ONLY provided analyses—no invention
- If evidence is sparse, note it honestly
- Avoid generic suggestions ("gift card," "socks") unless truly fitting
- Keep tone warm and helpful

