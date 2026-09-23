import type { ModelOptionProvider } from '@hermes/shared'
import { describe, expect, it } from 'vitest'

import {
  absentUsageEntries,
  compareProvidersByUsage,
  compareUsageTotals,
  hasUsageMetadata,
  modelUsageEntries,
  modelUsageEntry,
  providerUsageEntry,
  rankFamiliesByUsage,
  rankModelsByUsage,
  usageEntryForIds
} from './model-usage'

const row = (slug: string, models: string[], usage?: ModelOptionProvider['usage']): ModelOptionProvider => ({
  models,
  name: slug,
  slug,
  usage
})

/** Unix seconds, as the backend sends them. */
const SECOND = 1_700_000_000

describe('model usage metadata', () => {
  it('converts last_used_at from unix seconds to milliseconds', () => {
    const entry = modelUsageEntry(
      row('nous', ['hermes-4.5'], { 'hermes-4.5': { api_call_count: 2, last_used_at: SECOND } }),
      'hermes-4.5'
    )

    expect(entry?.lastUsedAt).toBe(SECOND * 1000)
  })

  it('reads omitted, null, empty and all-zero usage as "no usage"', () => {
    const bare = row('nous', ['hermes-4.5'])
    const empty = row('nous', ['hermes-4.5'], {})
    const zeros = row('nous', ['hermes-4.5'], { 'hermes-4.5': {} })
    const counted = row('nous', ['hermes-4.5'], { 'hermes-4.5': { api_call_count: 0 } })

    for (const provider of [bare, empty, zeros, counted]) {
      expect(modelUsageEntries(provider)).toEqual([])
      expect(providerUsageEntry(provider)).toBeNull()
      expect(hasUsageMetadata([provider])).toBe(false)
    }
  })

  it('survives a usage entry that carries a timestamp but no count', () => {
    const provider = row('nous', ['hermes-4.5'], { 'hermes-4.5': { last_used_at: SECOND } })

    expect(modelUsageEntry(provider, 'hermes-4.5')).toEqual({
      apiCallCount: 0,
      lastUsedAt: SECOND * 1000,
      model: 'hermes-4.5'
    })
  })

  it('keeps one model id on two provider rows as two independent entries', () => {
    const alpha = row('alpha', ['shared-1'], { 'shared-1': { api_call_count: 5, last_used_at: SECOND } })
    const beta = row('beta', ['shared-1'], { 'shared-1': { api_call_count: 90, last_used_at: SECOND + 600 } })

    expect(modelUsageEntry(alpha, 'shared-1')?.apiCallCount).toBe(5)
    expect(modelUsageEntry(beta, 'shared-1')?.apiCallCount).toBe(90)
    // The row with no history stays empty even though a sibling row uses the id.
    expect(modelUsageEntry(row('gamma', ['shared-1']), 'shared-1')).toBeNull()
  })

  it('ignores a usage key that is not on this provider row', () => {
    const provider = row('nous', ['hermes-4.5'], { 'hermes-mini': { api_call_count: 4 } })

    expect(modelUsageEntry(provider, 'hermes-4.5')).toBeNull()
    // The row still counts as used: it carries real history for a route it serves.
    expect(providerUsageEntry(provider)).toEqual({ apiCallCount: 4, lastUsedAt: null })
  })

  it('totals a row: summed calls, newest timestamp, and the route breakdown is ignored', () => {
    const provider = row('opencode-go', ['qwen3.7-plus'], {
      'qwen3.7-plus': {
        api_call_count: 547,
        last_used_at: SECOND + 60,
        routes: [
          { base_url: 'https://a.example/v1', api_call_count: 281, last_used_at: SECOND },
          { base_url: 'https://b.example/v1', api_call_count: 266, last_used_at: SECOND + 60 }
        ]
      },
      'qwen3.7-mini': { api_call_count: 3, last_used_at: SECOND - 60 }
    })

    expect(providerUsageEntry(provider)).toEqual({ apiCallCount: 550, lastUsedAt: (SECOND + 60) * 1000 })
  })

  it('ranks recency first and call count second', () => {
    const older = { apiCallCount: 900, lastUsedAt: SECOND }
    const newer = { apiCallCount: 2, lastUsedAt: SECOND + 1 }
    const tiedNewer = { apiCallCount: 7, lastUsedAt: SECOND + 1 }

    expect(compareUsageTotals(newer, older)).toBeLessThan(0)
    expect(compareUsageTotals(tiedNewer, newer)).toBeLessThan(0)
    expect(compareUsageTotals(newer, newer)).toBe(0)
    // No timestamp at all sorts behind any timestamped entry.
    expect(compareUsageTotals({ apiCallCount: 5, lastUsedAt: null }, older)).toBeGreaterThan(0)
  })

  it('floats used provider rows above unused ones without reordering the unused', () => {
    const used = row('nous', ['hermes-4.5'], { 'hermes-4.5': { api_call_count: 1, last_used_at: SECOND } })

    expect(compareProvidersByUsage(used, row('google', ['gemini-3.1-pro']))).toBeLessThan(0)
    expect(compareProvidersByUsage(row('google', ['gemini-3.1-pro']), used)).toBeGreaterThan(0)
    // Two unused rows tie, so the caller's own ordering survives.
    expect(compareProvidersByUsage(row('google', ['g']), row('nous', ['n']))).toBe(0)
  })
})

describe('usage ordering', () => {
  const provider = row('nous', ['hermes-4.5', 'hermes-mini', 'hermes-mini-fast', 'hermes-nano'], {
    'hermes-nano': { api_call_count: 1, last_used_at: SECOND + 100 },
    'hermes-mini': { api_call_count: 12, last_used_at: SECOND + 50 }
  })

  it('promotes used models, newest first, keeping the rest in curated order', () => {
    expect(rankModelsByUsage(provider, provider.models ?? [])).toEqual([
      'hermes-nano',
      'hermes-mini',
      'hermes-4.5',
      'hermes-mini-fast'
    ])
  })

  it('returns the list untouched when nothing was used', () => {
    const unused = row('google', ['gemini-3.1-pro', 'gemini-2.5-flash'])
    const models = unused.models ?? []

    expect(rankModelsByUsage(unused, models)).toEqual(models)
  })

  it('ranks a family by whichever member was used, so a used `-fast` sibling promotes its base row', () => {
    const fast = row('nous', ['hermes-4.5', 'hermes-4.5-fast', 'hermes-mini'], {
      'hermes-4.5-fast': { api_call_count: 4, last_used_at: SECOND }
    })

    expect(usageEntryForIds(fast, ['hermes-4.5', 'hermes-4.5-fast'])?.model).toBe('hermes-4.5-fast')

    const families = rankFamiliesByUsage(fast, [
      { fastId: 'hermes-4.5-fast', id: 'hermes-4.5' },
      { fastId: null, id: 'hermes-mini' }
    ])

    expect(families.map(family => family.id)).toEqual(['hermes-4.5', 'hermes-mini'])
  })

  it('keeps families with no usage in place behind the used ones', () => {
    const families = rankFamiliesByUsage(row('nous', ['a', 'b', 'c'], { c: { api_call_count: 3 } }), [
      { fastId: null, id: 'a' },
      { fastId: null, id: 'b' },
      { fastId: null, id: 'c' }
    ])

    expect(families.map(family => family.id)).toEqual(['c', 'a', 'b'])
  })
})

// The backend keeps usage per (provider row, model id) and it OUTLIVES the
// catalog: a model the provider has retired stays in `usage` and, when the
// route is renamed, the old id keeps its own entry. That history must not be
// lost — but it is history, not an option: usage proves a call happened, never
// that the id can be called again.
describe('history for routes the catalog no longer lists', () => {
  const dropped = row('deepseek', ['deepseek-v5', 'deepseek-v4-flash-fast'], {
    'deepseek-v4': { api_call_count: 12, last_used_at: SECOND + 500 },
    'deepseek-v4-flash': { api_call_count: 2246, last_used_at: SECOND },
    'deepseek-v5': { api_call_count: 3, last_used_at: SECOND + 900 }
  })

  it('returns exactly the used ids the current catalog dropped, newest first', () => {
    expect(absentUsageEntries(dropped).map(entry => entry.model)).toEqual(['deepseek-v4', 'deepseek-v4-flash'])
    // The listed model that WAS used never comes back as history…
    expect(absentUsageEntries(dropped).map(entry => entry.model)).not.toContain('deepseek-v5')
    // …and neither does the `-fast` variant that is still configured.
    expect(absentUsageEntries(dropped).map(entry => entry.model)).not.toContain('deepseek-v4-flash-fast')
  })

  it('keeps the entry whole, so a row can say how much history it has', () => {
    expect(absentUsageEntries(dropped)).toEqual([
      { apiCallCount: 12, lastUsedAt: (SECOND + 500) * 1000, model: 'deepseek-v4' },
      { apiCallCount: 2246, lastUsedAt: SECOND * 1000, model: 'deepseek-v4-flash' }
    ])
  })

  it('is empty when every used id is still listed', () => {
    const provider = row('nous', ['hermes-4.5', 'hermes-mini'], {
      'hermes-4.5': { api_call_count: 9, last_used_at: SECOND },
      'hermes-mini': { api_call_count: 4, last_used_at: SECOND + 20 }
    })

    expect(absentUsageEntries(provider)).toEqual([])
  })

  it('keys by provider row, so one dropped id on two rows stays two entries', () => {
    const alpha = row('alpha', ['keep'], { dropped: { api_call_count: 5, last_used_at: SECOND } })
    const beta = row('beta', ['keep'], { dropped: { api_call_count: 90, last_used_at: SECOND + 10 } })

    expect(absentUsageEntries(alpha).map(entry => entry.model)).toEqual(['dropped'])
    expect(absentUsageEntries(alpha)[0].apiCallCount).toBe(5)
    expect(absentUsageEntries(beta)[0].apiCallCount).toBe(90)
    // A row that never carried the id has no history to report for it.
    expect(absentUsageEntries(row('gamma', ['dropped']))).toEqual([])
  })

  it('reports history for a row with no catalog left at all', () => {
    const retired = row('opencode-go', [], { 'qwen3.7-plus': { api_call_count: 547, last_used_at: SECOND } })

    expect(absentUsageEntries(retired).map(entry => entry.model)).toEqual(['qwen3.7-plus'])
  })
})
