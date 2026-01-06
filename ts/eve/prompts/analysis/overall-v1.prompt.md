---
id: overall-v1
name: Overall Analysis
version: 1.0.0
category: analysis
tags: [core, comprehensive, multi-conversation]

prompt:
  source: markdown

model_preferences: [claude-sonnet-4-5-20250929, gpt-5-high, gemini-2.5-pro]
fallback_models: [gpt-5-high, gpt-5]

context_flexibility: high

context:
  default_pack: analyses-all-comprehensive
  alternatives: [analyses-year-personality, analyses-month-recent]

always_on: []

execution:
  mode: chatbot-streaming
  result_type: document
  result_title: "{{chat_title}} : Overall Analysis"

variables:
  required: [chat_title]
  optional: []
---

You have access to a set of conversation logs involving multiple participants. Each log may include: date/time range and total message count; a summary of the conversation's main content; a breakdown of each participant's messages, along with emotions, topics, entities, and humorous lines (when available).

Using these logs, please provide a comprehensive but concise analysis. Focus on the following points:

1) High-Level Narrative
- Briefly summarize the main story arcs that unfold in the conversations involving the participants: recurring projects or ventures, important events, key transitions, or any major shifts in tone over time.
- Identify any meaningful turning points in how the participants interact.

2) Emotional and Thematic Patterns
- Which emotions show up most frequently for each participant, and how do they change over time?
- Point out any recurring topics and describe how each participant typically feels about them.

3) Humor & Conflict Analysis
- Investigate how humor is used—who uses it more often and in what contexts?
- Identify any moments of conflict or tension among the participants; describe how the involved participants handle or resolve disagreements.

4) Communication Style Differences
- How do the participants differ in tone, message length, or emotional expression?
- Highlight moments when their styles complement or clash, with specific examples.

5) Interesting or Surprising Insights
- Share any unexpected findings (e.g., abrupt mood shifts, big spikes in message volume, unusual or repetitive words/phrases).
- If relevant, correlate certain topics with specific emotions or humor usage.

6) Advice or Observations
- Offer recommendations on how the participants might improve or maintain communication patterns (if applicable).

Important:
- Base your analysis solely on the provided conversation logs.
- If you make assumptions or inferences, clearly mark them as speculation.
- Keep your final output structured with clear section headings and organized content.
- Include all six sections in your analysis, clearly labeled.
- Be concise and focused.
- Prioritize quality insights over quantity of text.

FORMAT:
- Output a well-structured Markdown report with clear section headings for the six analysis points above (no JSON).

ARTIFACT:
- Create and save an artifact using the createDocument tool titled "{{chat_title}} : Overall Analysis" containing your final report.
