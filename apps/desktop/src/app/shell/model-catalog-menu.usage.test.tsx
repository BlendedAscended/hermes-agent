import type { ModelOptionProvider } from '@hermes/shared'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { DropdownMenu, DropdownMenuContent } from '@/components/ui/dropdown-menu'
import { $localModelsEnabled } from '@/store/local-models-flag'
import { $localRuntimeJobs } from '@/store/local-runtime-jobs'
import {
  $visibleModels,
  emptyProviderSentinelKey,
  modelVisibilityKey,
  setModelVisibilityOpen,
  setVisibleModels
} from '@/store/model-visibility'
import type { LocalRuntimeJob } from '@/types/hermes'

import { ModelCatalogMenu, type ModelMenuController } from './model-catalog-menu'

// Radix calls these on open; jsdom doesn't implement them.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelOptions = vi.fn()

vi.mock('@/hermes', () => ({
  getGlobalModelOptions: (...args: unknown[]) => getGlobalModelOptions(...args),
  getLocalModelsJobs: vi.fn(async () => {
    const { $localRuntimeJobs } = await import('@/store/local-runtime-jobs')

    return { jobs: [...$localRuntimeJobs.get()] }
  }),
  getLocalModelsStatus: vi.fn().mockResolvedValue({ loading: {} }),
  setApiRequestProfile: vi.fn()
}))

const SECOND = 1_700_000_000

/** Two routes on Google, the newer one used 40 times. */
const GOOGLE: ModelOptionProvider = {
  models: ['gemini-3.1-pro', 'gemini-2.5-flash'],
  name: 'Google',
  slug: 'google',
  usage: { 'gemini-2.5-flash': { api_call_count: 40, last_used_at: SECOND } }
}

/** Nous used more recently, on its small model. */
const NOUS: ModelOptionProvider = {
  models: ['hermes-4.5', 'hermes-mini'],
  name: 'Nous',
  slug: 'nous',
  usage: { 'hermes-mini': { api_call_count: 3, last_used_at: SECOND + 100 } }
}

/** Configured, authenticated — but never called. */
const OPENROUTER: ModelOptionProvider = {
  models: ['x-ai/grok-4.5'],
  name: 'OpenRouter',
  slug: 'openrouter'
}

beforeEach(() => {
  $visibleModels.set(null)
  $localRuntimeJobs.set([])
  $localModelsEnabled.set(true)
  setModelVisibilityOpen(false)
  getGlobalModelOptions.mockResolvedValue({ providers: [GOOGLE, NOUS, OPENROUTER] })
})

afterEach(() => {
  cleanup()
  $localRuntimeJobs.set([])
  vi.clearAllMocks()
})

function renderMenu(
  providers: ModelOptionProvider[],
  current: Partial<ModelMenuController['current']> = {}
): ModelMenuController {
  getGlobalModelOptions.mockResolvedValue({ providers })

  const controller: ModelMenuController = {
    applyPreset: vi.fn(),
    current: { effort: '', fast: false, model: '', provider: '', ...current },
    presetFor: () => ({}),
    select: vi.fn(),
    setOptions: vi.fn()
  }

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={client}>
      <DropdownMenu open>
        <DropdownMenuContent>
          <ModelCatalogMenu controller={controller} />
        </DropdownMenuContent>
      </DropdownMenu>
    </QueryClientProvider>
  )

  return controller
}

/** Menu rows in render order — group headings are labels, not rows. */
const menuRows = () => screen.getAllByRole('menuitem').map(element => element.textContent?.trim() ?? '')

const rowsMatching = (pattern: RegExp) => menuRows().filter(text => pattern.test(text))

const firstIndex = (pattern: RegExp) => menuRows().findIndex(text => pattern.test(text))

describe('usage-adaptive catalog default', () => {
  it('hides a provider with no route history and keeps the used ones', async () => {
    renderMenu([GOOGLE, NOUS, OPENROUTER])

    await screen.findByText(/gemini 3\.1/i)

    expect(rowsMatching(/hermes[\s-]mini/i).length).toBe(1)
    // Configured and authenticated, but never called: out of the default view.
    expect(rowsMatching(/grok/i).length).toBe(0)
    // …with the count of what it folded away on the escape hatch.
    expect(screen.getByText('Show all providers')).toBeTruthy()
  })

  it('promotes used routes ahead of the curated order, and used providers first', async () => {
    renderMenu([GOOGLE, NOUS])

    await screen.findByText(/hermes[\s-]mini/i)

    // Nous (newest call) before Google; the used route first inside each group.
    expect(firstIndex(/hermes[\s-]mini/i)).toBeLessThan(firstIndex(/hermes[\s-]4\.5/i))
    expect(firstIndex(/hermes[\s-]4\.5/i)).toBeLessThan(firstIndex(/gemini 3\.1/i))
    expect(firstIndex(/gemini 2\.5/i)).toBeLessThan(firstIndex(/gemini 3\.1/i))
  })

  it('breaks a recency tie on call count', async () => {
    const alpha: ModelOptionProvider = {
      models: ['alpha-used', 'alpha-idle'],
      name: 'Alpha',
      slug: 'alpha',
      usage: { 'alpha-used': { api_call_count: 5, last_used_at: SECOND } }
    }

    const beta: ModelOptionProvider = {
      models: ['beta-used', 'beta-idle'],
      name: 'Beta',
      slug: 'beta',
      usage: { 'beta-used': { api_call_count: 50, last_used_at: SECOND } }
    }

    renderMenu([alpha, beta])

    await screen.findByText(/alpha[\s-]used/i)

    expect(firstIndex(/beta[\s-]used/i)).toBeLessThan(firstIndex(/alpha[\s-]used/i))
  })

  it('keeps one model id on three providers as three rows, and hides only the row with no history', async () => {
    const alpha: ModelOptionProvider = {
      models: ['shared-1', 'shared-2'],
      name: 'Alpha',
      slug: 'alpha',
      usage: { 'shared-1': { api_call_count: 5, last_used_at: SECOND } }
    }

    const beta: ModelOptionProvider = {
      models: ['shared-1', 'beta-used'],
      name: 'Beta',
      slug: 'beta',
      usage: { 'beta-used': { api_call_count: 1, last_used_at: SECOND - 100 } }
    }

    const gamma: ModelOptionProvider = { models: ['shared-1'], name: 'Gamma', slug: 'gamma' }

    renderMenu([alpha, beta, gamma])

    await screen.findByText(/beta[\s-]used/i)

    // Alpha's row (used) and Beta's row (not used, same id) both render…
    expect(rowsMatching(/shared[\s-]1/i).length).toBe(2)
    // …and Gamma, whose only model is that same id, has no history of its own.
    expect(screen.queryByText('Gamma')).toBeNull()
  })

  it('keeps the current route visible even when its provider has no history', async () => {
    renderMenu([{ models: GOOGLE.models, name: 'Google', slug: 'google' }, NOUS], {
      model: 'gemini-2.5-flash',
      provider: 'google'
    })

    // The pinned row survives the fold; the rest of its provider does not.
    await screen.findByText(/gemini 2\.5/i)
    expect(rowsMatching(/gemini 3\.1/i).length).toBe(0)
    expect(rowsMatching(/hermes[\s-]mini/i).length).toBe(1)
  })

  it('reveals every folded provider through Show all', async () => {
    renderMenu([GOOGLE, NOUS, OPENROUTER])

    await screen.findByText(/gemini 3\.1/i)

    fireEvent.click(screen.getByText('Show all providers'))

    await waitFor(() => {
      expect(rowsMatching(/grok/i).length).toBe(1)
    })
    expect(screen.getByText('Show only providers you use')).toBeTruthy()
  })

  it('reaches a folded provider by search without Show all', async () => {
    renderMenu([GOOGLE, NOUS, OPENROUTER])

    await screen.findByText(/gemini 3\.1/i)

    fireEvent.change(screen.getByRole('textbox', { name: 'Search models' }), {
      target: { value: 'grok' }
    })

    await waitFor(() => {
      expect(rowsMatching(/grok/i).length).toBe(1)
    })
  })

  it('keeps an explicit Edit Models choice instead of the adaptive default', async () => {
    setVisibleModels(
      new Set([modelVisibilityKey('google', 'gemini-3.1-pro'), modelVisibilityKey('nous', 'hermes-4.5')])
    )

    renderMenu([GOOGLE, NOUS, OPENROUTER])

    // Google has no history, yet stays because the user chose it; Nous's USED
    // model stays hidden because the user did not.
    await screen.findByText(/gemini 3\.1/i)
    expect(rowsMatching(/hermes[\s-]4\.5/i).length).toBe(1)
    expect(rowsMatching(/hermes[\s-]mini/i).length).toBe(0)
    expect(rowsMatching(/gemini 2\.5/i).length).toBe(0)
    // Nothing was folded away, so there is nothing to reveal.
    expect(screen.queryByText('Show all providers')).toBeNull()
  })

  it('renders the curated list unchanged when no row carries usage', async () => {
    const unusedGoogle: ModelOptionProvider = { models: GOOGLE.models, name: 'Google', slug: 'google' }
    const unusedNtous: ModelOptionProvider = { models: NOUS.models, name: 'Nous', slug: 'nous' }

    renderMenu([unusedGoogle, unusedNtous, OPENROUTER])

    await screen.findByText(/gemini 3\.1/i)

    expect(rowsMatching(/grok/i).length).toBe(1)
    expect(rowsMatching(/hermes[\s-]mini/i).length).toBe(1)
    expect(screen.queryByText('Show all providers')).toBeNull()
  })
})

describe('used routes the catalog has dropped', () => {
  /** Retired here: `usage` still counts deepseek-v4-flash, `models` does not. */
  const RETIRED: ModelOptionProvider = {
    models: ['deepseek-v5'],
    name: 'DeepSeek',
    slug: 'deepseek',
    usage: { 'deepseek-v4-flash': { api_call_count: 2246, last_used_at: SECOND } }
  }

  /** The marked history row for a dropped id, if it rendered at all. */
  const historyRow = (model: string) =>
    screen.getAllByRole('menuitem').find(element => element.getAttribute('data-prior-use') === model)

  const markedRows = () => screen.getAllByRole('menuitem').filter(element => element.hasAttribute('data-prior-use'))

  it('keeps a used model the provider dropped as an unavailable row, never an option', async () => {
    const controller = renderMenu([RETIRED])

    await screen.findByText(/deepseek[ -]v5/i)

    const history = historyRow('deepseek-v4-flash')

    // Marked on the row itself: history, and unavailable — not a model you can pick.
    expect(history?.textContent).toMatch(/deepseek-v4-flash/)
    expect(history?.textContent).toMatch(/unavailable/i)
    expect(history?.getAttribute('aria-disabled')).toBe('true')
    // No hover submenu either: there are no options to configure for a route
    // the provider does not serve any more.
    expect(history?.hasAttribute('aria-haspopup')).toBe(false)

    // Activating it must not commit anything, and the id must not appear as a
    // second, plainly selectable row.
    fireEvent.click(history as Element)
    expect(controller.select).not.toHaveBeenCalled()
    expect(rowsMatching(/deepseek[ -]v4/i).length).toBe(1)
  })

  it('marks only the dropped id, leaving the live models plainly selectable', async () => {
    const controller = renderMenu([RETIRED])

    await screen.findByText(/deepseek[ -]v5/i)

    expect(markedRows().length).toBe(1)
    expect(rowsMatching(/deepseek[ -]v5/i)[0]).not.toMatch(/unavailable/i)

    fireEvent.click(screen.getByText(/deepseek[ -]v5/i))

    await waitFor(() => {
      expect(controller.select).toHaveBeenCalledWith('deepseek-v5', 'deepseek')
    })
  })

  it('keeps history inside a group the user curated, but never resurrects a provider they hid', async () => {
    const retiredGoogle: ModelOptionProvider = {
      models: ['gemini-3.1-pro'],
      name: 'Google',
      slug: 'google',
      usage: { 'gemini-2.0-pro': { api_call_count: 50, last_used_at: SECOND } }
    }

    // DeepSeek curated down to its live model; Google hidden outright (the
    // hide-all sentinel is how "I emptied this provider" is recorded).
    setVisibleModels(new Set([modelVisibilityKey('deepseek', 'deepseek-v5'), emptyProviderSentinelKey('google')]))

    renderMenu([RETIRED, retiredGoogle])

    await screen.findByText(/deepseek[ -]v5/i)

    // The curated group keeps its history row, still marked unavailable…
    expect(historyRow('deepseek-v4-flash')).toBeTruthy()
    // …while a provider the user hid stays out entirely, its history included.
    expect(screen.queryByText('Google')).toBeNull()
    expect(historyRow('gemini-2.0-pro')).toBeUndefined()
  })

  it('surfaces the history for a retired id that is searched for', async () => {
    renderMenu([RETIRED])

    await screen.findByText(/deepseek[ -]v5/i)

    fireEvent.change(screen.getByRole('textbox', { name: 'Search models' }), {
      target: { value: 'deepseek-v4' }
    })

    await waitFor(() => {
      expect(historyRow('deepseek-v4-flash')).toBeTruthy()
    })
    // Still history-only, still not selectable.
    expect(historyRow('deepseek-v4-flash')?.getAttribute('aria-disabled')).toBe('true')
  })
})

describe('local downloads under the adaptive default', () => {
  const DOWNLOAD_JOB: LocalRuntimeJob = {
    job_id: 'dl1',
    kind: 'model-download',
    target: 'Qwen3.8 Flash Next (UD-Q4_K_XL)',
    model_id: 'qwen3.8-flash-next',
    status: 'running',
    phase: 'downloading',
    detail: '',
    total_bytes: 100,
    done_bytes: 41,
    percent: 41,
    error: null
  }

  it('still shows a running download when its provider was folded out of the default list', async () => {
    const local: ModelOptionProvider = {
      models: ['Qwen3.6-27B-UD-Q4_K_XL'],
      name: 'Local',
      slug: 'llamacpp'
    }

    $localRuntimeJobs.set([DOWNLOAD_JOB])
    renderMenu([local, NOUS])

    await screen.findByText(/hermes[\s-]mini/i)

    // No Local group was drawn (no history), so the download keeps its own.
    expect(screen.getByText('Qwen3.8 Flash Next (UD-Q4_K_XL)')).toBeTruthy()
    expect(rowsMatching(/qwen3\.6/i).length).toBe(0)
  })
})
