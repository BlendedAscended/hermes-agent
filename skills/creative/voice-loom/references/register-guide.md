# Register Guide

Voice Loom draws word palettes from four registers. Every palette spans at least two. Cross register exposure is how vocabulary expands.

---

## Register 1: Blunt

**Character:** Short words. Direct meaning. Anglo-Saxon roots when possible. No decoration. No hedging.

**Word Bank:**
hit, break, stuck, grind, ship, cut, push, pull, drop, run, burn, snap, lock, sink, rise, fall, kick, bite, crack, split, grip, fold, strip, rip, slam, slap, drag, haul, dump, chuck, shift, shake, rattle, shove, fling, hurl, yank, wrench, wedge, jam

**When this register works:**
- Casual social media posts
- Direct opinions and hot takes
- Landing page hero copy
- Email subject lines

**When this register backfires:**
- Formal business communication
- Technical documentation
- Content for non-native English speakers (blunt words often carry idiomatic weight)

**Examples:**
- Formal: "The system experienced performance degradation."
- Blunt: "It broke."

- Formal: "We are iterating rapidly toward product-market fit."
- Blunt: "We are shipping fast and seeing what sticks."

---

## Register 2: Technical

**Character:** Precise terms. Domain specific vocabulary. Assumes the reader shares the domain. No hand holding.

**Word Bank:**
bottleneck, regression, throughput, latency, overhead, abstraction, encapsulation, idempotent, deterministic, stochastic, convergence, divergence, saturation, threshold, inflection, interpolation, extrapolation, amortization, atomicity, consistency, isolation, durability, normalization, denormalization, serialization, deserialization, orchestration, choreography, federation, sharding, replication, partitioning, compaction, eviction, invalidation, prefetching, memoization, hydration, dehydration, reconciliation

**When this register works:**
- Engineering blog posts
- Architecture decision records
- Writing for a technical audience that already knows the domain
- When precision matters more than accessibility

**When this register backfires:**
- General audience content
- LinkedIn posts (unless audience is exclusively engineers)
- Landing pages (technical terms signal "this is not for you")

**Examples:**
- General: "The system slows down when too many requests arrive at once."
- Technical: "Throughput degrades under contention as the lock manager hits saturation."

- General: "We saved the same calculation results to avoid redoing the work."
- Technical: "We memoized the expensive derivation with a bounded cache using LRU eviction."

---

## Register 3: Poetic

**Character:** Evocative words. Metaphorical language. Imagery that creates a picture. Words chosen for resonance as much as meaning.

**Word Bank:**
chasm, gravity well, dead reckoning, foothold, headwind, tailwind, undertow, riptide, bedrock, fault line, watershed, crucible, forge, loom, weave, thread, tapestry, canvas, palette, mosaic, symphony, crescendo, dissonance, resonance, echo, ripple, cascade, tide, current, drift, vessel, anchor, compass, north star, lodestar, beacon, lighthouse, frontier, horizon, precipice, summit, plateau, valley, clearing, thicket, undergrowth, canopy

**When this register works:**
- Long-form essays
- Talks and presentations
- Brand storytelling
- Founder letters
- Content that needs to inspire rather than inform

**When this register backfires:**
- Technical documentation (poetic words confuse, not clarify)
- Email (reads as pretentious)
- Time-sensitive communication
- Any context where clarity is the primary goal

**Examples:**
- Direct: "The team lost direction after the pivot."
- Poetic: "The compass spun without a north star. Every direction looked the same."

- Direct: "The market is crowded with competitors."
- Poetic: "Every clearing has a dozen footprints already. The thicket is where the path starts."

---

## Register 4: Colloquial

**Character:** Conversational words. Idioms and phrases. The way people actually talk. Feels like a message from a friend.

**Word Bank:**
come up short, miss the mark, drop the ball, pick up steam, gain ground, lose the plot, fall through the cracks, move the needle, raise the bar, lower the bar, set the tone, call the shots, pull the plug, pull the trigger, get the ball rolling, keep the lights on, put out fires, spin plates, wear many hats, drink from the firehose, boil the ocean, move the goalposts, shift gears, change lanes, go off the rails, jump the shark, phone it in, mail it in, wing it, fly by the seat of your pants, shoot from the hip, cut to the chase, get down to brass tacks, bottom line, at the end of the day, when the rubber meets the road, where the buck stops

**When this register works:**
- LinkedIn posts
- Twitter threads
- Internal team communication
- Conversational email
- Content that needs warmth and accessibility

**When this register backfires:**
- Formal business communication
- Academic writing
- Investor updates (too casual signals lack of seriousness)
- Cross-cultural communication (idioms do not translate)

**Examples:**
- Formal: "The project encountered unexpected challenges during implementation."
- Colloquial: "The project hit a wall. We did not see it coming."

- Formal: "We should prioritize the most impactful initiatives."
- Colloquial: "Let us figure out what actually moves the needle and do that first."

---

## Palette Construction Rules

1. **Minimum two registers per statement.** One register is a thesaurus. Two registers is a palette.
2. **Register distribution by medium:**

| Medium | Blunt | Technical | Poetic | Colloquial |
|--------|-------|-----------|--------|-----------|
| linkedin | 30% | 10% | 10% | 50% |
| essay | 15% | 20% | 40% | 25% |
| email | 25% | 5% | 5% | 65% |
| landing | 40% | 5% | 10% | 45% |
| script | 20% | 10% | 30% | 40% |
| tweet-thread | 35% | 5% | 5% | 55% |
| pitch | 25% | 30% | 15% | 30% |

3. **No AI tell words.** Scan every word against humanizer patterns 1 through 34. Words like "delve", "showcase", "tapestry", "crucial", "foster", and "pivotal" are banned from palettes.

4. **Five to eight words per palette.** No fewer (lacks variety). No more (choice paralysis).

5. **Vary register composition across rows.** Mix blunt+poetic on one row, technical+colloquial on the next.

## Common Palette Construction Mistakes

1. **Same-register synonym cycling.** "Fast, quick, rapid, swift" is not a palette. It is four synonyms from one register. Add registers: "Fast, surgical, no-lag, blink-and-you-miss-it."

2. **Fake poetic words.** "Synergistic", "holistic", "transformative." These are AI tells, not real poetic register.

3. **Technical words as decoration.** Do not put "deterministic" in a palette unless the statement is actually about determinism.

4. **Over-colloquializing.** One or two colloquial words per palette is enough. Three makes the writing sound like a corporate team building exercise.
