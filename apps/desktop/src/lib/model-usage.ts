import type { ModelOptionProvider, ModelUsage } from '@hermes/shared'

/**
 * Lifetime main-loop usage for one model on ONE catalog provider row.
 *
 * Identity is (provider row, model id): the backend keys `usage` per provider
 * row, so the same model id configured under two providers arrives as two rows
 * with two independent entries and must never be merged. Inside a row the
 * totals already sum every base URL the model was billed through.
 */
export interface ModelUsageTotals {
  /** Lifetime calls for this model on this row. */
  apiCallCount: number
  /** Most recent call in MILLISECONDS (the backend sends unix seconds — the
   *  conversion happens here so no caller has to remember), or null when the
   *  backend carries no timestamp. */
  lastUsedAt: null | number
}

export interface ModelUsageEntry extends ModelUsageTotals {
  model: string
}

/** Normalize one backend entry. `usage` is omitted entirely for a row with no
 *  history, and an entry carrying neither a count nor a timestamp says nothing
 *  — both read as "no usage", never as a zero-valued row. */
function toEntry(model: string, usage: ModelUsage | null | undefined): ModelUsageEntry | null {
  const apiCallCount = usage?.api_call_count ?? 0
  const lastUsedAt = typeof usage?.last_used_at === 'number' ? usage.last_used_at * 1000 : null

  if (apiCallCount <= 0 && lastUsedAt === null) {
    return null
  }

  return { apiCallCount, lastUsedAt, model }
}

/** Every model id with real usage on this provider row, unordered. */
export function modelUsageEntries(provider: ModelOptionProvider): ModelUsageEntry[] {
  const usage = provider.usage

  if (!usage) {
    return []
  }

  const entries: ModelUsageEntry[] = []

  for (const [model, entry] of Object.entries(usage)) {
    const normalized = toEntry(model, entry)

    if (normalized) {
      entries.push(normalized)
    }
  }

  return entries
}

/** One model's usage on this provider row, or null when it has none. */
export function modelUsageEntry(provider: ModelOptionProvider, model: string): ModelUsageEntry | null {
  return toEntry(model, provider.usage?.[model])
}

/** The row's own totals: the newest call across its models and the sum of their
 *  calls (the same aggregation the backend does across base URLs). Null when
 *  the row has no history at all. */
export function providerUsageEntry(provider: ModelOptionProvider): ModelUsageTotals | null {
  const entries = modelUsageEntries(provider)

  if (entries.length === 0) {
    return null
  }

  let apiCallCount = 0
  let lastUsedAt: null | number = null

  for (const entry of entries) {
    apiCallCount += entry.apiCallCount

    if (entry.lastUsedAt !== null && (lastUsedAt === null || entry.lastUsedAt > lastUsedAt)) {
      lastUsedAt = entry.lastUsedAt
    }
  }

  return { apiCallCount, lastUsedAt }
}

/** True when ANY provider row carries usage — the switch that turns adaptive
 *  presentation on. A catalog whose backend omitted (or emptied) `usage` — an
 *  older runtime, an unreadable store, rows nobody could attribute — fails
 *  open and keeps today's ordering and visibility. */
export function hasUsageMetadata(providers: readonly ModelOptionProvider[]): boolean {
  return providers.some(provider => modelUsageEntries(provider).length > 0)
}

/** Newest first, then busiest. Ties fall through to the caller's own order. */
export function compareUsageTotals(a: ModelUsageTotals, b: ModelUsageTotals): number {
  const recency = (b.lastUsedAt ?? -1) - (a.lastUsedAt ?? -1)

  if (recency !== 0) {
    return recency
  }

  return b.apiCallCount - a.apiCallCount
}

/** The usage that represents a group of ids — a model AND its `-fast`/snapshot
 *  sibling are one menu family, so the family ranks by whichever member was
 *  actually used. */
export function usageEntryForIds(
  provider: ModelOptionProvider,
  ids: readonly (null | string | undefined)[]
): ModelUsageEntry | null {
  let best: ModelUsageEntry | null = null

  for (const id of ids) {
    if (!id) {
      continue
    }

    const entry = modelUsageEntry(provider, id)

    if (entry && (best === null || compareUsageTotals(entry, best) < 0)) {
      best = entry
    }
  }

  return best
}

/** Used routes on this row that the CURRENT catalog no longer lists — retired,
 *  renamed or dropped ids. The history is real (this profile did call them) but
 *  usage is NOT evidence that a route is still routable, so callers render
 *  these as unavailable, never as selectable rows. Keyed by (row, model id)
 *  like everything else, so one retired id on two provider rows stays two
 *  entries. Newest first.
 *
 *  Entries are compared against the raw `models` list, so a `-fast` sibling
 *  that is still configured counts as listed — only ids the catalog has
 *  genuinely dropped come back. */
export function absentUsageEntries(provider: ModelOptionProvider): ModelUsageEntry[] {
  const listed = new Set(provider.models ?? [])

  return modelUsageEntries(provider)
    .filter(entry => !listed.has(entry.model))
    .sort((a, b) => compareUsageTotals(a, b))
}

/** Stable promotion: used items first (newest, then busiest), the untouched
 *  remainder in the order it was given. Nothing used → the list is returned as
 *  given, so an un-adaptive catalog is byte-for-byte what it was. */
function promoteByUsage<T>(items: readonly T[], totalsFor: (item: T) => ModelUsageTotals | null): T[] {
  const ranked: { index: number; item: T; totals: ModelUsageTotals }[] = []

  items.forEach((item, index) => {
    const totals = totalsFor(item)

    if (totals) {
      ranked.push({ index, item, totals })
    }
  })

  if (ranked.length === 0) {
    return [...items]
  }

  ranked.sort((a, b) => compareUsageTotals(a.totals, b.totals) || a.index - b.index)

  const rank = new Map<T, number>()

  ranked.forEach((row, position) => rank.set(row.item, position))

  return [...items].sort((a, b) => {
    const left = rank.get(a)
    const right = rank.get(b)

    if (left === undefined && right === undefined) {
      return 0
    }

    if (left === undefined) {
      return 1
    }

    if (right === undefined) {
      return -1
    }

    return left - right
  })
}

/** Used models first, ranked by recency then call count; the rest keep their
 *  curated order. */
export function rankModelsByUsage(provider: ModelOptionProvider, models: readonly string[]): string[] {
  return promoteByUsage(models, model => modelUsageEntry(provider, model))
}

/** `rankModelsByUsage` for catalog-menu rows, where a base model and its
 *  `-fast` sibling collapse into one family that ranks as a unit. */
export function rankFamiliesByUsage<T extends { fastId: null | string; id: string }>(
  provider: ModelOptionProvider,
  families: readonly T[]
): T[] {
  return promoteByUsage(families, family => usageEntryForIds(provider, [family.id, family.fastId]))
}

/** Provider-group order: rows with history first, newest then busiest. Callers
 *  keep their own name ordering as the tie-break. */
export function compareProvidersByUsage(a: ModelOptionProvider, b: ModelOptionProvider): number {
  const left = providerUsageEntry(a)
  const right = providerUsageEntry(b)

  if (!left && !right) {
    return 0
  }

  if (!left) {
    return 1
  }

  if (!right) {
    return -1
  }

  return compareUsageTotals(left, right)
}
