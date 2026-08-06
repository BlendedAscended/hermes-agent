---
name: voice-loom
description: "Use when AI output falls flat. Decompose for human voice."
version: 1.0.0
author: Veldon Lab (San Ghotra)
license: MIT
metadata:
  hermes:
    tags: [writing, voice, content, humanization, vocabulary, prose, decomposition, scaffolding]
    related_skills: [humanizer, sandeep-universal-style, codex-content-generation]
---

# Voice Loom

## Overview

Voice Loom inverts the standard AI writing pipeline. Instead of AI generating prose and a humanizer cleaning the fingerprints, Voice Loom makes AI the decomposer and you the author. AI breaks content into cognitive building blocks — statements, word palettes, emotional anchors. You read the scaffold, pick from the palette, and write the final prose in your own voice. The AI never writes your words. It creates the conditions for you to find them.

This skill occupies empty space. The market has humanizers (post-production cleanup) and AI writers (generation). Voice Loom does neither. It decomposes AI output into a structured scaffold that triggers vocabulary expansion and forces you to revisit each thought before writing it yourself.

The central metaphor: a loom takes individual threads and weaves them into fabric. Voice Loom takes individual word choices and rhetorical options and lets you weave them into prose. The threads come from the AI. The weaving is yours.

## When to Use

Load this skill when:
- AI Content Factory or vflow output feels flat and you want to write the final version yourself
- You have research or context from AI and want to express it in your own voice
- You want to expand your vocabulary by seeing alternative ways to say the same thing
- You are adapting content across mediums (LinkedIn post to email to landing page to script)
- You are warming up before a writing session and want a structured vocabulary drill

Do not use when:
- The AI output already sounds human and on voice — ship it
- Time pressure makes the decomposition overhead unreasonable
- Content is purely factual with no voice dimension (API docs, changelogs, data tables)

## Input

Voice Loom accepts content from multiple sources:

1. **Inline paste.** The user pastes text directly into the chat.
2. **File path.** Point at a `.md`, `.txt`, or `.json` file containing the content.
3. **Pipeline handoff.** Piped from a prior tool output or from the AI Content Factory.

Optional parameters:
- `target_medium` (string): One of `linkedin`, `essay`, `email`, `landing`, `script`, `tweet-thread`, `pitch`. Shifts word palette register distribution and statement structure.
- `voice_profile` (path): Path to a project voice profile file. Falls back to `sandeep-universal-style` defaults when absent.
- `output_mode` (string): Which output format to produce. Defaults to `classic-table`.

Multiple pieces of content can be processed in one invocation. Each generates its own table.

## Core Mechanism

Voice Loom produces a structured table. Each row holds one statement and its scaffolding.

### Statement

An atomic idea extracted from the input content. One complete thought per row. Short enough to hold in working memory while you scan the palette. Usually one sentence. Occasionally a fragment when the medium calls for it.

Statements are extracted in logical order so reading down the statement column gives you the full argument arc before you touch a single word choice.

### Word Palette

A list of 5 to 8 words or phrases per statement, drawn from across four registers:

| Register | Character | Example Words | Best For |
|----------|----------|--------------|----------|
| Blunt | Short, direct, Anglo-Saxon | hit, break, stuck, grind, ship | Casual posts, direct opinions |
| Technical | Precise, domain specific | bottleneck, regression, throughput | Engineering audience |
| Poetic | Evocative, metaphorical | chasm, gravity well, dead reckoning | Essays, talks, brand |
| Colloquial | Conversational, idiomatic | come up short, miss the mark, drop the ball | LinkedIn, Twitter |

Each word in the palette can:
- Replace a single word in the statement (synonym)
- Replace multiple words or a phrase (phrase swap)
- Reframe the entire statement angle without being a direct synonym (conceptual alternative)

The palette always spans at least two registers per statement. Cross register exposure is how vocabulary expands. You see a blunt word next to a poetic one and your brain starts doing alchemy.

### Emotional Anchor

A single word per statement that sets the emotional temperature. Not a synonym. Not a rhetorical label. A mood.

Examples: `momentum`, `friction`, `tension`, `relief`, `curiosity`, `conviction`, `doubt`, `urgency`, `calm`, `defiance`, `wonder`, `fatigue`, `hope`.

The anchor primes your brain before you pick words from the palette. When a statement about tool limitations carries the anchor `friction`, you write it differently than when the same statement carries `opportunity`. The anchor is the most important column because it governs which words in the palette feel right.

### The Table

```
| Statement | Word Palette | Anchor |
|-----------|-------------|--------|
| Adoption is accelerating across enterprise teams | surge, climb, outpace, ramp | momentum |
| But the tools still stumble on edge cases | falter, miss the mark, trip over | friction |
| The gap between demo and production is where teams get stuck | chasm, bottleneck, dead zone | tension |
```

Reading down the table is the cognitive loop: statement to scan palette to feel anchor to choose words to next statement. By the time you reach the bottom, you have already rewritten the content in your head.

## Process

1. **Extract statements.** Read the input content. Break it into atomic ideas. Each idea gets one row. Preserve logical order. If the content is a research brief, extract the key claims. If it is a draft, extract the core assertions. Each statement must hold a complete thought on its own.

2. **Generate word palettes.** For each statement, produce 5 to 8 alternative words or phrases drawn from at least two registers. Include words that replace single terms, words that replace phrases, and words that reframe the angle entirely. Follow the target medium preferences.

3. **Assign emotional anchors.** For each statement, choose one word that captures the mood. Ask: what temperature does this statement run at? What feeling lives behind the fact? Assign the anchor word.

4. **Select output mode.** Pick the output format based on the context. Default: classic table. If the text has strong emotional progression, use emotional arc map. If the text needs memorable punch, use rhetorical device overlay.

5. **Output the scaffold.** Write the markdown file containing the chosen output format. Include a brief header noting the input source and any parameters used. Do not write the final prose. That is the human's job.

6. **Human assembly.** The user reads the scaffold, picks from the palettes, and writes the final prose in their own voice. Voice Loom stops at the scaffold. The human starts at the scaffold.

## Output Modes

Voice Loom supports six output formats. Select by setting `output_mode` or let the target medium imply the best fit.

| # | Mode | Description | Best For |
|---|------|------------|----------|
| 1 | Classic Table | Three-column table: statement, word palette, anchor | General purpose |
| 2 | Expandable Cards | Collapsible sections, one per statement | Long-form content |
| 3 | Side by Side Candidates | Original + 3 human-angle rewrites | When AI version is flat |
| 4 | Emotional Arc Map | Statements ordered by emotional temperature | Speeches, scripts |
| 5 | Rhetorical Device Overlay | Statements annotated with device suggestions | Memorable posts, talks |
| 6 | Vocabulary Drill | Extracted high-value words across all statements | Vocabulary warmup |

Full specifications for each mode live in `references/output-modes.md`. Templates for modes 1, 4, and 5 live in `templates/`.

### Mode Selection by Target Medium

| Target Medium | Recommended Mode | Fallback |
|--------------|-----------------|----------|
| linkedin | Rhetorical Overlay (5) | Classic Table (1) |
| essay | Emotional Arc Map (4) | Expandable Cards (2) |
| email | Classic Table (1) | Side by Side (3) |
| landing | Rhetorical Overlay (5) | Classic Table (1) |
| script | Emotional Arc Map (4) | Classic Table (1) |
| tweet-thread | Classic Table (1) | Rhetorical Overlay (5) |
| pitch | Rhetorical Overlay (5) | Side by Side (3) |

## The Shakespeare Layer

Voice Loom embeds three capabilities drawn from classical rhetoric and the Shakespearean tradition of working with language as raw material.

### Vocabulary Expansion

The word palette always spans registers. Blunt words next to poetic ones. Technical terms next to colloquial phrases. Cross register exposure is the engine of vocabulary growth. You do not learn new words from a list. You learn them when you see them alongside words you already know, doing the same job differently. The palette format forces that juxtaposition.

### Rhetorical Devices

Voice Loom can annotate statements with rhetorical device suggestions. Not every statement needs a device. The skill suggests one when the statement's structure creates a natural opening. The full device library lives in `references/rhetorical-devices.md`. Twelve devices: Antithesis, Anaphora, Chiasmus, Litotes, Zeugma, Assonance, Polysyndeton, Asyndeton, Epistrophe, Enallage, Auxesis, Metanoia.

The skill suggests devices. You decide whether to use them. An unused device is not a failure. A forced device is worse than no device.

### Emotional Anchoring

Every statement gets an emotional anchor word. This is the Shakespeare layer at its most essential. Before iambic pentameter set rhythm, Shakespeare decided what each line needed to feel. The meter followed the emotion. The anchor word serves the same function: it sets the temperature before you pick the words.

## The Da Vinci Layer

Da Vinci worked across disciplines. He did not have separate tools for painting, engineering, and anatomy. He had a way of seeing that applied everywhere. Voice Loom works the same way.

### Cross Medium Composability

The `target_medium` parameter shifts the output without changing the core mechanism. Same skill. Same table. Different fingerprint per medium. Register distribution shifts, statement structure adapts, default output mode changes. But the core loop — statement to palette to anchor to choose to write — stays the same whether you are writing a LinkedIn post or a video script.

Voice Loom is not just an AI Content Factory plugin. It is a general purpose writing tool you can invoke from any project, any context, any time you are producing text.

### Project Voice Profile System

Voice Loom checks for a `voice-profile.md` file in the project root or at a path you specify. When the file exists, the skill reads it and applies the project's voice rules to the word palettes and anchor assignments. An example voice profile lives at `templates/voice-profile.example.md`.

When no voice profile exists, Voice Loom falls back to the default voice from `sandeep-universal-style`: short declarative sentences, active voice, no filler, no em dashes, open compounds.

## Integration with AI Content Factory

```
AI Content Factory (research + context generation)
        ↓
vflow / Sian voice (first pass)
        ↓
   ┌──── Does it sound right? ────┐
   ↓ YES                          ↓ NO
   publish                  Voice Loom (decompose + scaffold)
                                   ↓
                            Human assembly (you write)
                                   ↓
                            humanizer (final AI tell cleanup)
                                   ↓
                            publish
```

Voice Loom is the escape hatch. When the first pass feels flat, you do not prompt the AI to try harder. You decompose and take over the writing yourself.

For standalone use: `Any content → Voice Loom → Human writes → optional humanizer → publish`

## Common Pitfalls

1. **Treating the scaffold as the final output.** The table is not prose. It is a scaffold. You must write the final version yourself. If you ask AI to write from the scaffold, you defeat the purpose.

2. **Overloading word palettes.** 5 to 8 words per statement maximum. More creates choice paralysis. The point is to trigger one or two new thoughts, not to exhaust every synonym.

3. **Forcing rhetorical devices on every statement.** Not every line needs antithesis or chiasmus. Suggest devices where they fit naturally. A device on every line reads like a poetry textbook, not a human.

4. **Skipping the emotional anchor.** The anchor is the most cognitively important column. It governs register choice. Without it, the word palette is just a thesaurus. With it, the palette gains direction.

5. **Using only one register in the palette.** Minimum two registers per statement. Cross register exposure is how vocabulary expands.

6. **Decomposing content that is already good.** If the AI output sounds human and on voice, ship it. Voice Loom has overhead. Use it when you need it.

7. **Writing passive or vague statements.** The statements should be active and declarative. If the raw material is already passive, the palette will not fix it.

8. **Neglecting target medium shifts.** A landing page palette looks different from an essay palette. Set `target_medium`.

## Verification Checklist

After Voice Loom produces a scaffold, verify:

- [ ] Every statement is one atomic idea
- [ ] Every statement could stand alone as a complete thought
- [ ] Every word palette has 5 to 8 items
- [ ] Every word palette spans at least two registers
- [ ] Every statement has one emotional anchor word
- [ ] Anchor words feel accurate to the statement's temperature
- [ ] Rhetorical devices are suggested sparingly, not forced on every row
- [ ] The output mode matches the context and target medium
- [ ] No AI tell words appear in the statements (check against humanizer patterns)
- [ ] Statement order preserves the logical arc of the original content
- [ ] The output file is a scaffold, not final prose

## Attribution

Voice Loom was designed by San Ghotra (Veldon Lab) as a pre-production tool for the AI Content Factory pipeline. The Shakespeare layer draws from classical rhetoric — the tradition of inventio (invention) and elocutio (style) in the five canons. The Da Vinci cross domain composability layer applies the principle that a good tool works the same way across every medium, adapting its fingerprint without changing its mechanism.
