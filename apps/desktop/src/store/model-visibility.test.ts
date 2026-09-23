import type { ModelOptionProvider } from '@hermes/shared'
import { describe, expect, it } from 'vitest'

import {
  adaptiveDefaultVisibleKeys,
  collapseModelFamilies,
  defaultVisibleKeys,
  effectiveVisibleKeys,
  emptyProviderSentinelKey,
  isProviderSentinel,
  modelVisibilityKey,
  resolveVisibleKeys,
  setProviderVisibility,
  toggleModelVisibility
} from './model-visibility'

const provider = (slug: string, models: string[]): ModelOptionProvider => ({
  models,
  name: slug,
  slug
})

describe('model visibility', () => {
  it('keeps newly configured providers visible when stored choices are stale', () => {
    const stored = new Set([modelVisibilityKey('copilot', 'claude-sonnet-4.6')])

    const visible = effectiveVisibleKeys(stored, [
      provider('copilot', ['claude-sonnet-4.6']),
      provider('local-ollama', ['qwen3:latest', 'llama3.2:latest'])
    ])

    expect(visible.has(modelVisibilityKey('copilot', 'claude-sonnet-4.6'))).toBe(true)
    expect(visible.has(modelVisibilityKey('local-ollama', 'qwen3:latest'))).toBe(true)
    expect(visible.has(modelVisibilityKey('local-ollama', 'llama3.2:latest'))).toBe(true)
  })

  it('does not re-add models from a provider that already has stored choices', () => {
    const stored = new Set([modelVisibilityKey('local-ollama', 'qwen3:latest')])

    const visible = effectiveVisibleKeys(stored, [provider('local-ollama', ['qwen3:latest', 'llama3.2:latest'])])

    expect(visible.has(modelVisibilityKey('local-ollama', 'qwen3:latest'))).toBe(true)
    expect(visible.has(modelVisibilityKey('local-ollama', 'llama3.2:latest'))).toBe(false)
  })

  it('preserves hidden-provider sentinel without re-adding defaults', () => {
    // User explicitly hid all models for "nous" — sentinel marks this choice.
    const stored = new Set([emptyProviderSentinelKey('nous')])

    const visible = effectiveVisibleKeys(stored, [
      provider('nous', ['hermes-3-llama-3.1-70b', 'hermes-3-llama-3.1-8b']),
      provider('ollama', ['qwen3:latest'])
    ])

    expect(visible.has(modelVisibilityKey('nous', 'hermes-3-llama-3.1-70b'))).toBe(false)
    expect(visible.has(modelVisibilityKey('nous', 'hermes-3-llama-3.1-8b'))).toBe(false)
    // Sentinel itself is stripped from the result.
    expect(visible.has(emptyProviderSentinelKey('nous'))).toBe(false)
    // Other providers still get defaults.
    expect(visible.has(modelVisibilityKey('ollama', 'qwen3:latest'))).toBe(true)
  })

  it('restores model when toggling on after hiding all', () => {
    // Simulates: user hid all "nous" models, then toggles one back on.
    const stored = new Set([emptyProviderSentinelKey('nous'), modelVisibilityKey('ollama', 'qwen3:latest')])

    // After toggle: sentinel removed, one model added.
    const afterToggle = new Set(stored)
    afterToggle.delete(emptyProviderSentinelKey('nous'))
    afterToggle.add(modelVisibilityKey('nous', 'hermes-3-llama-3.1-70b'))

    const visible = effectiveVisibleKeys(afterToggle, [
      provider('nous', ['hermes-3-llama-3.1-70b', 'hermes-3-llama-3.1-8b']),
      provider('ollama', ['qwen3:latest'])
    ])

    expect(visible.has(modelVisibilityKey('nous', 'hermes-3-llama-3.1-70b'))).toBe(true)
    expect(visible.has(modelVisibilityKey('nous', 'hermes-3-llama-3.1-8b'))).toBe(false)
  })

  it('folds a date-pinned snapshot into its rolling alias when present', () => {
    const families = collapseModelFamilies(['claude-opus-4-5', 'claude-opus-4-5-20251101'])

    expect(families.map(f => f.id)).toEqual(['claude-opus-4-5'])
  })

  it('keeps a date-pinned snapshot standing alone when it has no alias', () => {
    const families = collapseModelFamilies(['claude-opus-4-5-20251101', 'claude-haiku-4-5-20251001'])

    expect(families.map(f => f.id)).toEqual(['claude-opus-4-5-20251101', 'claude-haiku-4-5-20251001'])
  })

  it('sentinel key helper produces correct format', () => {
    expect(emptyProviderSentinelKey('openai')).toBe('openai::')
    expect(isProviderSentinel('openai::')).toBe(true)
    expect(isProviderSentinel('openai::gpt-4o')).toBe(false)
  })

  it('resolveVisibleKeys preserves sentinels that effectiveVisibleKeys strips', () => {
    const stored = new Set([emptyProviderSentinelKey('nous')])
    const providers = [provider('nous', ['hermes-x', 'hermes-y']), provider('ollama', ['qwen3:latest'])]

    const resolved = resolveVisibleKeys(stored, providers)
    expect(resolved.has(emptyProviderSentinelKey('nous'))).toBe(true)
    expect(resolved.has(modelVisibilityKey('nous', 'hermes-x'))).toBe(false)
    // Un-customized providers still expand to their defaults.
    expect(resolved.has(modelVisibilityKey('ollama', 'qwen3:latest'))).toBe(true)

    // Display variant drops the sentinel.
    expect(effectiveVisibleKeys(stored, providers).has(emptyProviderSentinelKey('nous'))).toBe(false)
  })
})

describe('toggleModelVisibility', () => {
  const providers = [provider('openai', ['gpt-a', 'gpt-b']), provider('nous', ['hermes-x', 'hermes-y'])]

  // Drive the handler the way the dialog does: feed each result back in as the
  // next `stored`, so the persisted set is what the next toggle starts from.
  const apply = (stored: Set<string> | null, slug: string, model: string) =>
    toggleModelVisibility(stored, providers, slug, model)

  it('records a hide-all sentinel when the last model of a provider is toggled off', () => {
    let stored: Set<string> | null = null
    stored = apply(stored, 'openai', 'gpt-a')
    stored = apply(stored, 'openai', 'gpt-b')

    expect(stored.has(emptyProviderSentinelKey('openai'))).toBe(true)
    expect(effectiveVisibleKeys(stored, providers).has(modelVisibilityKey('openai', 'gpt-a'))).toBe(false)
    expect(effectiveVisibleKeys(stored, providers).has(modelVisibilityKey('openai', 'gpt-b'))).toBe(false)
  })

  it('keeps a hidden provider hidden when a different provider is toggled (regression for #43485)', () => {
    // Hide ALL of nous — its sentinel is now stored.
    let stored: Set<string> | null = null
    stored = apply(stored, 'nous', 'hermes-x')
    stored = apply(stored, 'nous', 'hermes-y')
    expect(stored.has(emptyProviderSentinelKey('nous'))).toBe(true)

    // Toggle a model in another provider. nous must NOT snap back on.
    stored = apply(stored, 'openai', 'gpt-a')

    expect(stored.has(emptyProviderSentinelKey('nous'))).toBe(true)
    const visible = effectiveVisibleKeys(stored, providers)
    expect(visible.has(modelVisibilityKey('nous', 'hermes-x'))).toBe(false)
    expect(visible.has(modelVisibilityKey('nous', 'hermes-y'))).toBe(false)
  })

  it('clears only the toggled provider sentinel when a model is re-enabled', () => {
    let stored: Set<string> | null = new Set([emptyProviderSentinelKey('openai'), emptyProviderSentinelKey('nous')])

    stored = apply(stored, 'openai', 'gpt-a')

    expect(stored.has(emptyProviderSentinelKey('openai'))).toBe(false)
    expect(stored.has(emptyProviderSentinelKey('nous'))).toBe(true)
    const visible = effectiveVisibleKeys(stored, providers)
    expect(visible.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(true)
    expect(visible.has(modelVisibilityKey('nous', 'hermes-x'))).toBe(false)
  })

  it('re-enabling one model of a hidden-all provider restores ONLY that model, not the curated defaults', () => {
    // openai hidden-all, nous untouched.
    let stored: Set<string> | null = new Set([emptyProviderSentinelKey('openai')])

    stored = apply(stored, 'openai', 'gpt-a')

    const visible = effectiveVisibleKeys(stored, providers)
    expect(visible.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(true)
    // gpt-b is NOT restored — "you hid everything, you get back only what you re-enable".
    expect(visible.has(modelVisibilityKey('openai', 'gpt-b'))).toBe(false)
  })

  it('re-hiding the last re-enabled model re-adds the sentinel (full round-trip)', () => {
    let stored: Set<string> | null = new Set([emptyProviderSentinelKey('openai')])

    // Re-enable gpt-a (clears sentinel, set = {gpt-a}), then toggle it back off.
    stored = apply(stored, 'openai', 'gpt-a')
    expect(stored.has(emptyProviderSentinelKey('openai'))).toBe(false)
    stored = apply(stored, 'openai', 'gpt-a')

    expect(stored.has(emptyProviderSentinelKey('openai'))).toBe(true)
    expect(effectiveVisibleKeys(stored, providers).has(modelVisibilityKey('openai', 'gpt-a'))).toBe(false)
  })

  it('toggling from an empty (non-null) stored set adds the model without expanding defaults', () => {
    // Empty-but-not-null = "everything hidden". resolveVisibleKeys short-circuits to {}.
    const stored = new Set<string>()

    const next = apply(stored, 'openai', 'gpt-a')

    expect(next.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(true)
    // No curated defaults were expanded for any provider.
    expect(next.has(modelVisibilityKey('openai', 'gpt-b'))).toBe(false)
    expect(next.has(modelVisibilityKey('nous', 'hermes-x'))).toBe(false)
  })

  it('toggling off one default model from null stored keeps the rest of the curated defaults', () => {
    // null = "never customized": resolveVisibleKeys expands all defaults first.
    const next = apply(null, 'openai', 'gpt-a')

    expect(next.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(false)
    expect(next.has(modelVisibilityKey('openai', 'gpt-b'))).toBe(true)
    expect(next.has(modelVisibilityKey('nous', 'hermes-x'))).toBe(true)
    // Other models remain, so no sentinel.
    expect(next.has(emptyProviderSentinelKey('openai'))).toBe(false)
  })

  it('tolerates a provider with zero models (defensive — dialog filters these out)', () => {
    const ps = [provider('empty', []), provider('openai', ['gpt-a'])]
    const next = toggleModelVisibility(new Set([modelVisibilityKey('openai', 'gpt-a')]), ps, 'empty', 'ghost')

    // No crash; the phantom key is recorded but no defaults are invented.
    expect([...next].some(k => k.startsWith('empty::') && !isProviderSentinel(k))).toBe(true)
    expect(next.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(true)
  })
})

describe('resolveVisibleKeys', () => {
  const providers = [provider('openai', ['gpt-a', 'gpt-b']), provider('nous', ['hermes-x', 'hermes-y'])]

  it('returns the curated defaults verbatim for null stored', () => {
    expect(resolveVisibleKeys(null, providers)).toEqual(defaultVisibleKeys(providers))
  })

  it('returns an empty set for an empty (non-null) stored set', () => {
    expect([...resolveVisibleKeys(new Set(), providers)]).toEqual([])
  })
})

describe('featured defaults', () => {
  const featuredProvider = (slug: string, models: string[], featured_models: string[]): ModelOptionProvider => ({
    featured_models,
    models,
    name: slug,
    slug
  })

  it('defaults to the featured shortlist when a provider publishes one', () => {
    const nous = featuredProvider(
      'nous',
      ['anthropic/opus', 'anthropic/haiku', 'google/gemini', 'x-ai/grok'],
      ['anthropic/opus', 'google/gemini', 'x-ai/grok']
    )

    const visible = defaultVisibleKeys([nous])

    // Featured are visible; the non-featured model is hidden by default.
    expect(visible.has(modelVisibilityKey('nous', 'anthropic/opus'))).toBe(true)
    expect(visible.has(modelVisibilityKey('nous', 'google/gemini'))).toBe(true)
    expect(visible.has(modelVisibilityKey('nous', 'x-ai/grok'))).toBe(true)
    expect(visible.has(modelVisibilityKey('nous', 'anthropic/haiku'))).toBe(false)
  })

  it('falls back to top-N when a provider ships no featured list', () => {
    const plain = provider('ollama', ['qwen3:latest', 'llama3.2:latest'])

    const visible = defaultVisibleKeys([plain])

    // No featured_models → every model stays a default (top-N, N ≫ 2 here).
    expect(visible.has(modelVisibilityKey('ollama', 'qwen3:latest'))).toBe(true)
    expect(visible.has(modelVisibilityKey('ollama', 'llama3.2:latest'))).toBe(true)
  })

  it('ignores an empty featured list and falls back to top-N', () => {
    const plain = featuredProvider('ollama', ['qwen3:latest', 'llama3.2:latest'], [])

    const visible = defaultVisibleKeys([plain])

    expect(visible.has(modelVisibilityKey('ollama', 'qwen3:latest'))).toBe(true)
    expect(visible.has(modelVisibilityKey('ollama', 'llama3.2:latest'))).toBe(true)
  })
})

describe('setProviderVisibility', () => {
  const providers = [provider('openai', ['gpt-a', 'gpt-b']), provider('nous', ['hermes-x', 'hermes-y'])]

  it('enabling a provider makes every one of its models visible', () => {
    // Start from a hidden-all openai; flip it on.
    const stored = new Set([emptyProviderSentinelKey('openai')])

    const next = setProviderVisibility(stored, providers, 'openai', true)

    const visible = effectiveVisibleKeys(next, providers)
    expect(visible.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(true)
    expect(visible.has(modelVisibilityKey('openai', 'gpt-b'))).toBe(true)
    // Sentinel is cleared.
    expect(next.has(emptyProviderSentinelKey('openai'))).toBe(false)
  })

  it('disabling a provider hides all its models and records the sentinel', () => {
    const next = setProviderVisibility(null, providers, 'openai', false)

    expect(next.has(emptyProviderSentinelKey('openai'))).toBe(true)
    const visible = effectiveVisibleKeys(next, providers)
    expect(visible.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(false)
    expect(visible.has(modelVisibilityKey('openai', 'gpt-b'))).toBe(false)
  })

  it('leaves other providers untouched (their sentinels survive)', () => {
    const stored = new Set([emptyProviderSentinelKey('nous')])

    // Turn openai fully on; nous must stay hidden.
    const next = setProviderVisibility(stored, providers, 'openai', true)

    expect(next.has(emptyProviderSentinelKey('nous'))).toBe(true)
    const visible = effectiveVisibleKeys(next, providers)
    expect(visible.has(modelVisibilityKey('nous', 'hermes-x'))).toBe(false)
    expect(visible.has(modelVisibilityKey('openai', 'gpt-a'))).toBe(true)
  })

  it('round-trips: enable then disable returns to a clean hidden-all', () => {
    const enabled = setProviderVisibility(null, providers, 'openai', true)
    const disabled = setProviderVisibility(enabled, providers, 'openai', false)

    expect(disabled.has(emptyProviderSentinelKey('openai'))).toBe(true)
    // No stray real keys left for the provider.
    expect([...disabled].some(k => k.startsWith('openai::') && !isProviderSentinel(k))).toBe(false)
  })

  it('collapses model families to one key per family when enabling', () => {
    // A base + its -fast sibling collapse to a single family row/key.
    const ps = [provider('nous', ['model', 'model-fast'])]

    const next = setProviderVisibility(null, ps, 'nous', true)

    expect(next.has(modelVisibilityKey('nous', 'model'))).toBe(true)
    // The -fast sibling is represented by its base family, not its own key.
    expect(next.has(modelVisibilityKey('nous', 'model-fast'))).toBe(false)
  })
})

// The usage-adaptive default only ever REPLACES the curated list for a catalog
// the user has never customized. It is presentation: nothing here writes to the
// stored set, so an explicit choice can never be overwritten by usage.
describe('adaptive default visibility', () => {
  const usageProvider = (
    slug: string,
    models: string[],
    usage: ModelOptionProvider['usage']
  ): ModelOptionProvider => ({ models, name: slug, slug, usage })

  const SECOND = 1_700_000_000

  it('drops a provider with no route history and keeps the used provider + its used route', () => {
    const providers = [
      usageProvider('google', ['gemini-3.1-pro', 'gemini-2.5-flash'], undefined),
      usageProvider('nous', ['hermes-4.5', 'hermes-mini'], {
        'hermes-mini': { api_call_count: 3, last_used_at: SECOND }
      })
    ]

    const keys = adaptiveDefaultVisibleKeys(providers)

    expect(keys.has(modelVisibilityKey('nous', 'hermes-mini'))).toBe(true)
    expect(keys.has(modelVisibilityKey('nous', 'hermes-4.5'))).toBe(true)
    expect([...keys].some(key => key.startsWith('google::'))).toBe(false)
  })

  it('fails open to the curated list when no row carries usage', () => {
    const providers = [
      usageProvider('google', ['gemini-3.1-pro', 'gemini-2.5-flash'], undefined),
      usageProvider('nous', ['hermes-4.5'], {})
    ]

    expect(adaptiveDefaultVisibleKeys(providers)).toEqual(defaultVisibleKeys(providers))
  })

  it('keeps duplicate model ids separate: usage on one row never marks another row used', () => {
    const providers = [
      usageProvider('alpha', ['shared-1', 'shared-2'], {
        'shared-1': { api_call_count: 5, last_used_at: SECOND }
      }),
      usageProvider('beta', ['shared-1', 'shared-2'], undefined)
    ]

    const keys = adaptiveDefaultVisibleKeys(providers)

    expect(keys.has(modelVisibilityKey('alpha', 'shared-1'))).toBe(true)
    expect([...keys].some(key => key.startsWith('beta::'))).toBe(false)
  })

  it('adds a used `-fast` sibling as its base family key, never an unrenderable id', () => {
    const providers = [
      usageProvider('nous', ['hermes-4.5', 'hermes-4.5-fast'], {
        'hermes-4.5-fast': { api_call_count: 2, last_used_at: SECOND }
      })
    ]

    const keys = adaptiveDefaultVisibleKeys(providers)

    expect(keys.has(modelVisibilityKey('nous', 'hermes-4.5'))).toBe(true)
    expect(keys.has(modelVisibilityKey('nous', 'hermes-4.5-fast'))).toBe(false)
  })

  it('honours featured_models for a used provider while still adding the used route', () => {
    const providers = [
      usageProvider('openrouter', ['flagship-a', 'flagship-b', 'long-tail-c'], undefined),
      {
        featured_models: ['flagship-a'],
        models: ['flagship-a', 'flagship-b', 'long-tail-c'],
        name: 'OpenRouter',
        slug: 'openrouter',
        usage: { 'long-tail-c': { api_call_count: 9, last_used_at: SECOND } }
      }
    ]

    const keys = adaptiveDefaultVisibleKeys(providers)

    expect(keys.has(modelVisibilityKey('openrouter', 'flagship-a'))).toBe(true)
    expect(keys.has(modelVisibilityKey('openrouter', 'long-tail-c'))).toBe(true)
    expect(keys.has(modelVisibilityKey('openrouter', 'flagship-b'))).toBe(false)
  })

  it('never writes to the stored set — an explicit choice keeps its own keys', () => {
    const providers = [
      usageProvider('google', ['gemini-3.1-pro'], undefined),
      usageProvider('nous', ['hermes-4.5'], {
        'hermes-4.5': { api_call_count: 1, last_used_at: SECOND }
      })
    ]

    const stored = new Set([modelVisibilityKey('google', 'gemini-3.1-pro')])

    expect(adaptiveDefaultVisibleKeys(providers).has(modelVisibilityKey('google', 'gemini-3.1-pro'))).toBe(false)
    // The stored preference is what the UI reads once it exists — untouched.
    expect(effectiveVisibleKeys(stored, providers).has(modelVisibilityKey('google', 'gemini-3.1-pro'))).toBe(true)
  })
})
