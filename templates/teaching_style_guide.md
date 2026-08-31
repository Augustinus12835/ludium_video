# Teaching Style Guide

> **Docs only — NOT read by the pipeline.** The style block actually injected
> into script-generation prompts is the `STYLE_KEY_POINTS` constant in
> `scripts/generate_scripts.py`. Edit that constant to change model behavior;
> keep this document in sync for human readers.

## Overview
This guide defines the teaching and narration style for automated educational video generation. It is content-agnostic and applies to any subject area.

---

## Core Teaching Philosophy

### First Principles Approach
- Strip away jargon to reveal fundamental concepts
- Start with the human problem that a concept solves
- Build understanding logically from basics to complex
- Emphasize "why" before "what" before "how"

### Learner-Centric Focus
- Use learner's own experiences and knowledge as anchors
- Relate abstract concepts to tangible, familiar objects
- Address common misconceptions proactively
- Assume intelligent but non-expert audience

---

## Voice & Tone Characteristics

### Concise Conversational Style
**Goal:** Natural delivery, zero waste. Every word earns its place.

### Conversational Elements (Use Strategically)
**Sentence Starters (1-2 per script):**
- "Here we'll look at..."
- "Think about..."

**Transitions (Minimal):**
- "So..." (start of new concept)
- "Now..." (shift focus)

**Emphasis (When Critical):**
- "Key point:" (before crucial concept)
- "Remember:" (callback to earlier point)

### BANNED contrastive constructions

**Never** use the formula "It's not X, it's Y" or any variant. This has become a tell of AI-generated writing. Banned patterns include:

- "It's not X, it's Y."
- "It's not just X, it's Y."
- "X isn't Y, it's Z."
- "Not just X — Y."
- "This isn't merely X; it's Y."

**Define the concept directly instead.** State what the thing *is*; don't define it by what it isn't.

| Banned (AI-tell) | Use (direct) |
|---|---|
| "It's not a number, it's a vector." | "A vector has magnitude and direction." |
| "Bitcoin isn't just money, it's a ledger." | "Bitcoin is a public ledger of transactions." |
| "This isn't merely fast — it's parallel." | "It runs in parallel across all cores." |
| "Forms aren't ideas in minds, they're real entities." | "For Plato, Forms exist independently of any mind." |

### Efficient Speech Patterns
- **Contractions:** Use (it's, we're, don't) but sparingly
- **NO redundancy:** Say it once, move on
- **NO rhetorical questions:** State facts directly
- **NO pet abstractions:** "framing", "machinery", and "load-bearing" are overused across the
  channel — avoid them (unless literal); name the concrete thing instead
- **NO patronizing emphasis:** "You need to understand...", "This is very important..." — just
  state the fact
- **Active voice:** Always

### Inclusive Language (Minimal)
- **"We":** Only when doing something together ("We calculate...")
- **"You":** Only when action required ("You'll notice...")
- **Default:** Direct statements ("This has known properties.")

---

## Teaching Techniques

### 1. Analogy-Driven Explanation (Efficient Version)

**Pattern:**
```
Concept → Brief Analogy → Application (3 sentences max)

Example:
"[Concept] works like [familiar thing]. [Brief comparison].
[How it applies in practice]."
```

### 2. Progressive Building (Streamlined)

**Structure:**
1. State concept clearly
2. Show why it matters
3. Give one example
4. Move on

**Avoid:**
- Long setup stories
- Multiple similar examples
- Excessive context building
- Restating what was just said

### 3. Direct Examples (Precision Required)

**Format:**
```
Statement → Numbers → Result → Move on

"[Operation]: [specific values] equals [result]."

NOT: "So if we want to [do something], we take [value],
and we [operation] it by [value], right? So [value]
[operation] [value] gives us [result]."
```

**Rules:**
- State once, no repetition
- Use specific numbers when relevant
- No verbal cushioning
- Next point immediately

### 4. Visual Reference Integration (Minimal)

**Only when necessary:**
```
"See the left side - [what's shown]."
"Right side shows [contrast]."
```

Frame changes happen naturally - no need to narrate them.

---

## Word Economy Principles

### Every Word Must Earn Its Place

No verbal cushioning or setup before the point ("So what this means is...", "What we want to
do is..." — say "This means..." or just do it), and no patronizing emphasis ("Make sure
you...", "You need to understand..." — state the fact).

### One-Time Rule

**State each concept ONCE. Never repeat unless:**
1. It's a different frame showing application
2. It's the final summary connecting concepts
3. It's a calculation step requiring verification

---

## Structural Framework

### Word Count Formula
```
Target seconds × 2.5 words/second = max word count

Example:
10 seconds → 25 words max
30 seconds → 75 words max
60 seconds → 150 words max
```

### Video Structure
1. **Opening (Frame 0):** Hook + preview. **BANNED openers** — never start a video with any of these patterns:
   - "What if I told you..." / "What if..."
   - "Imagine..." / "Picture this..."
   - "Have you ever wondered..."
   - "You might think..." / "You probably think..."
   - Any rhetorical question as the first sentence

   Instead, open with a concrete fact, a specific number, a historical event, or a direct declarative statement. Jump straight into substance.
2. **Build (Frames 1-3):** Establish foundations
3. **Deepen (Frames 4-8):** Add complexity, examples
4. **Apply (Frames 9-N):** Practical takeaways
5. **Close (Final Frame):** Synthesis, integrated with content

---

## Quality Checklist

Before finalizing any script, verify:

**Timing & Length:**
- [ ] Total duration matches target
- [ ] Each frame within time budget (10-60 seconds max)
- [ ] Word count per frame: seconds × 2.5 words

**Word Economy:**
- [ ] NO rhetorical questions
- [ ] NO verbal cushioning or patronizing emphasis
- [ ] NO pet abstractions ("framing", "machinery", "load-bearing")
- [ ] Each concept stated ONCE
- [ ] Frame 0 does NOT start with "What if", "Imagine", "Picture this", or any rhetorical question
- [ ] NO "It's not X, it's Y" (or variants like "not just X, but Y") — define directly instead

**Precision:**
- [ ] Direct statements, active voice throughout
- [ ] Technical terms defined in one sentence
- [ ] No redundant explanations

**Teaching Effectiveness:**
- [ ] Starts with clear concept statement
- [ ] Uses one brief analogy (if needed)
- [ ] Includes specific example
- [ ] Ends with key takeaway (no fluff)

---

*This style guide is content-agnostic and applies to educational videos across all subject areas.*
