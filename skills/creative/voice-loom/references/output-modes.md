# Output Mode Specifications

Voice Loom supports six output formats. Each mode serves a different cognitive or contextual need. Select by setting `output_mode` or let the target medium imply the best fit.

---

## Mode 1: Classic Table

**Description:** Three column table with statement, word palette, and emotional anchor. The default. The workhorse.

**When to use:** General purpose. Any content type. When you do not know which mode to pick, pick this one.

**Structure:**

```
## Voice Loom Scaffold
**Source:** [filename or "inline paste"]
**Target Medium:** [medium]
**Mode:** Classic Table

| # | Statement | Word Palette | Anchor |
|---|-----------|-------------|--------|
| 1 | [statement] | [word1, word2, word3, word4, word5] | [anchor] |
| 2 | [statement] | [word1, word2, word3, word4, word5] | [anchor] |
```

**Example:**

| # | Statement | Word Palette | Anchor |
|---|-----------|-------------|--------|
| 1 | AI coding tools are accelerating adoption across enterprise | surge, climb, outpace, ramp, pick up steam, snowball | momentum |
| 2 | They handle boilerplate faster than any human can type | grind through, churn out, absorb, autopilot, mechanical | relief |
| 3 | But they stumble when the problem has no training data precedent | falter, miss the mark, break down, trip over, come up short | friction |
| 4 | The gap between demo magic and production reality is wide | chasm, bottleneck, dead zone, gravity well, valley | tension |
| 5 | Most teams figure this out the hard way | learn, discover, stumble into, get burned by, earn | humility |
| 6 | The ones who ship put guardrails around the AI, not trust in it | fence, constrain, bound, box in, leash | pragmatism |

---

## Mode 2: Expandable Cards

**Description:** Each statement rendered as a collapsible markdown section. Palette and anchor nested inside. One card per statement.

**When to use:** Long form content where you want to focus on one statement at a time. Essays. Deep work writing sessions. When the classic table creates too much visual noise.

**Structure:**

```
## Voice Loom Scaffold
**Source:** [filename]
**Target Medium:** essay
**Mode:** Expandable Cards

<details>
<summary><b>1.</b> [statement text] <i>[anchor]</i></summary>

**Blunt:** word1, word2
**Technical:** word3, word4
**Poetic:** word5, word6
**Colloquial:** word7, word8

</details>

<details>
<summary><b>2.</b> [statement text] <i>[anchor]</i></summary>
...
</details>
```

---

## Mode 3: Side by Side Candidates

**Description:** Original AI text on the left. Three human angle rewrites on the right. Not a scaffold. A set of directions to choose from.

**When to use:** When the AI output is flat but you do not want to start from scratch. When you need to see the same idea from multiple angles.

**Structure:**

```
## Voice Loom Scaffold
**Source:** [filename]
**Target Medium:** linkedin
**Mode:** Side by Side Candidates

### Statement: [statement text]

| Angle | Rewrite Candidate |
|-------|------------------|
| Direct | [blunt, no-nonsense version] |
| Contrarian | [version that challenges the premise] |
| Story | [version that opens with a narrative hook] |
```

**Example:**

### Statement: AI coding tools are accelerating adoption across enterprise

| Angle | Rewrite Candidate |
|-------|------------------|
| Direct | Enterprises are adopting AI coding tools faster than anyone predicted. Not experimenting. Full rollouts. |
| Contrarian | Everyone says AI coding tools are taking over. The numbers say otherwise. Most teams try them for a month and quietly stop. |
| Story | Last week a CTO told me her team shipped a feature in two days that would have taken two weeks. The AI wrote 80% of the code. She wrote the architecture. |

---

## Mode 4: Emotional Arc Map

**Description:** Statements ordered by emotional temperature with the arc visualized. Each statement tagged with anchor word and intensity level.

**When to use:** Speeches, video scripts, narrative essays. Any content where emotional trajectory matters.

**Structure:**

```
## Voice Loom Scaffold
**Source:** [filename]
**Target Medium:** script
**Mode:** Emotional Arc Map

### Emotional Arc

[confidence] ████░░░░░░
[friction]   ██░░░░░░░░
[tension]    ██████░░░░
[relief]     ███░░░░░░░
[conviction] ████████░░

### Statement Map

| # | Statement | Anchor | Intensity | Arc Position |
|---|-----------|--------|-----------|-------------|
| 1 | [text] | confidence | 4/10 | Opening |
| 2 | [text] | friction | 2/10 | Build |
| 3 | [text] | tension | 6/10 | Peak |
| 4 | [text] | relief | 3/10 | Release |
| 5 | [text] | conviction | 8/10 | Close |

### Arc Notes
- Peak at statement 3: [note on why]
- Key transition: 3 to 4 shifts from [emotion] to [emotion]
- Close lands on [emotion]: [note]
```

---

## Mode 5: Rhetorical Device Overlay

**Description:** Each statement annotated with a suggested device from the 12 device library. Application column shows a concrete usable phrase.

**When to use:** Content that needs to be memorable, not just readable. LinkedIn posts, pitch decks, talks.

**Structure:**

```
## Voice Loom Scaffold
**Source:** [filename]
**Target Medium:** linkedin
**Mode:** Rhetorical Device Overlay

| # | Statement | Word Palette | Device | Application | Anchor |
|---|-----------|-------------|--------|------------|--------|
| 1 | [text] | [palette] | Antithesis | "X, not Y" | momentum |
| 2 | [text] | [palette] | — | — | friction |
| 3 | [text] | [palette] | Asyndeton | "Test. Ship. Repeat." | tension |
```

Application column shows a concrete phrase using the device. Not a description. A usable fragment.

---

## Mode 6: Vocabulary Drill

**Description:** Extracts high value, unusual, or register expanding words from across all statements. No rewriting. Pure vocabulary exposure.

**When to use:** As a warmup before writing. When vocabulary expansion is the primary goal.

**Structure:**

```
## Voice Loom Scaffold
**Source:** [filename]
**Mode:** Vocabulary Drill

### Words Worth Knowing

| Word | Definition | Register | In Context |
|------|-----------|----------|-----------|
| [word] | [definition] | [register] | "[sentence from context]" |

### Phrases Worth Stealing

| Phrase | Meaning | Register | In Context |
|--------|---------|----------|-----------|
| [phrase] | [meaning] | [register] | "[full sentence]" |
```

---

## Mode Selection Decision Tree

```
Is the content narrative (story, speech, script)?
  YES → Mode 4: Emotional Arc Map
  NO  → Does the content need to be memorable and punchy?
           YES → Mode 5: Rhetorical Device Overlay
           NO  → Is the AI output flat and you need direction?
                    YES → Mode 3: Side by Side Candidates
                    NO  → Is this long-form content?
                             YES → Mode 2: Expandable Cards
                             NO  → Is vocabulary the primary goal?
                                      YES → Mode 6: Vocabulary Drill
                                      NO  → Mode 1: Classic Table
```
