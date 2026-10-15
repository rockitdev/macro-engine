import { useEffect, useState } from "react"
import { AlertCircle, Check, ChevronDown, Minus, Plus } from "lucide-react"

import { ApiError, fitMeal, getChains, getSettings, setMealsPerDay } from "@/api"
import { Field } from "@/components/field"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { formatGrams, formatKcal } from "@/lib/format"
import type { Day, FitMacroBudget, FitMealResult, FitOrder } from "@/types"

// ── helpers ──────────────────────────────────────────────────────────────────

function macroLabel(key: string) {
  if (key === "kcal") return "kcal"
  if (key === "protein_g") return "protein"
  if (key === "carb_g") return "carbs"
  if (key === "fat_g") return "fat"
  return key
}

function blowLabel(key: string) {
  if (key === "kcal") return "calories"
  if (key === "protein_g") return "protein"
  if (key === "carb_g") return "carbs"
  if (key === "fat_g") return "fat"
  return key
}

function parseOptNum(s: string): number | null {
  if (s.trim() === "") return null
  const n = Number(s)
  return Number.isFinite(n) && n >= 0 ? n : null
}

// ── sub-components ───────────────────────────────────────────────────────────

function MacroRow({
  label,
  value,
  fillClass,
}: {
  label: string
  value: number
  fillClass: string
}) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${fillClass}`}>
      {label}: {label === "kcal" ? formatKcal(value) : `${formatGrams(value)} g`}
    </span>
  )
}

function OrderCard({ order, rank }: { order: FitOrder; rank: number }) {
  const [open, setOpen] = useState(rank === 1)
  const medal = rank === 1 ? "🥇" : rank === 2 ? "🥈" : "🥉"

  return (
    <div className="rounded-lg border bg-card">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-lg leading-none" aria-hidden="true">
            {medal}
          </span>
          <span className="truncate font-medium">
            {order.items.map((it) => it.name).join(" + ")}
          </span>
        </div>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="border-t px-4 pb-4 pt-3 space-y-3">
          {order.items.map((item, i) => (
            <div key={i}>
              <p className="text-sm font-medium">{item.name}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{item.portion}</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <MacroRow label="kcal" value={item.macros.kcal} fillClass="bg-mist text-foreground" />
                <MacroRow label="protein" value={item.macros.protein_g} fillClass="bg-spinach/15 text-spinach" />
                <MacroRow label="carbs" value={item.macros.carb_g} fillClass="bg-grain/20 text-foreground" />
                <MacroRow label="fat" value={item.macros.fat_g} fillClass="bg-roast/15 text-foreground" />
              </div>
            </div>
          ))}

          {order.items.length > 1 && (
            <div className="border-t pt-3">
              <p className="text-xs text-muted-foreground uppercase tracking-wide mb-1.5">Total</p>
              <div className="flex flex-wrap gap-1.5">
                <MacroRow label="kcal" value={order.total.kcal} fillClass="bg-mist text-foreground" />
                <MacroRow label="protein" value={order.total.protein_g} fillClass="bg-spinach/15 text-spinach" />
                <MacroRow label="carbs" value={order.total.carb_g} fillClass="bg-grain/20 text-foreground" />
                <MacroRow label="fat" value={order.total.fat_g} fillClass="bg-roast/15 text-foreground" />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Stepper for a small integer value (e.g. meals left today). */
function Stepper({
  value,
  min,
  max,
  onChange,
  label,
}: {
  value: number
  min: number
  max: number
  onChange: (v: number) => void
  label: string
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label={`Decrease ${label}`}
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-input bg-background text-muted-foreground transition-colors hover:bg-mist disabled:opacity-40"
          disabled={value <= min}
          onClick={() => onChange(Math.max(min, value - 1))}
        >
          <Minus className="h-4 w-4" />
        </button>
        <span className="w-6 text-center text-sm font-medium tabular-nums">{value}</span>
        <button
          type="button"
          aria-label={`Increase ${label}`}
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-input bg-background text-muted-foreground transition-colors hover:bg-mist disabled:opacity-40"
          disabled={value >= max}
          onClick={() => onChange(Math.min(max, value + 1))}
        >
          <Plus className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}

// ── main component ───────────────────────────────────────────────────────────

export function FitMealDialog({
  open,
  onOpenChange,
  day,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  day: Day | null
}) {
  // chains
  const [chains, setChains] = useState<string[]>([])
  const [chainsError, setChainsError] = useState<string | null>(null)

  // meals setting from server (loaded when dialog opens)
  const [settingsMpd, setSettingsMpd] = useState(3)

  // form state
  const [restaurant, setRestaurant] = useState("")
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [kcal, setKcal] = useState("")
  const [protein, setProtein] = useState("")
  const [carbs, setCarbs] = useState("")
  const [fat, setFat] = useState("")

  // per-meal controls
  const [mealsLeft, setMealsLeft] = useState(settingsMpd)
  const [includeTreats, setIncludeTreats] = useState(false)
  const [savingMpd, setSavingMpd] = useState(false)

  // results
  const [result, setResult] = useState<FitMealResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // fetch chains + settings when dialog opens
  useEffect(() => {
    if (!open) return
    setResult(null)
    setError(null)
    setRestaurant("")
    setOverrideOpen(false)
    setIncludeTreats(false)
    Promise.all([
      getChains().then(({ chains: c }) => setChains(c)).catch((err: unknown) => {
        setChainsError(err instanceof ApiError ? err.message : "Could not load chains")
      }),
      getSettings().then(({ meals_per_day }) => {
        setSettingsMpd(meals_per_day)
        setMealsLeft(meals_per_day)
      }).catch(() => {
        // Settings endpoint may not exist yet — fall back silently
      }),
    ])
  }, [open])

  // pre-fill budget fields from day remaining when override panel opens
  useEffect(() => {
    if (!overrideOpen) return
    const rem = day?.remaining
    setKcal(rem?.kcal != null ? String(Math.max(0, rem.kcal)) : "")
    setProtein(rem?.protein_g != null ? String(Math.max(0, rem.protein_g)) : "")
    setCarbs(rem?.carb_g != null ? String(Math.max(0, rem.carb_g)) : "")
    setFat(rem?.fat_g != null ? String(Math.max(0, rem.fat_g)) : "")
  }, [overrideOpen, day])

  async function submit() {
    if (!restaurant) {
      setError("Pick a restaurant first.")
      return
    }
    setError(null)
    setBusy(true)
    setResult(null)

    let override: FitMacroBudget | null = null
    if (overrideOpen) {
      override = {
        kcal: parseOptNum(kcal),
        protein_g: parseOptNum(protein),
        carb_g: parseOptNum(carbs),
        fat_g: parseOptNum(fat),
      }
    }

    try {
      const res = await fitMeal({
        restaurant,
        remaining: override,
        meals_left: mealsLeft,
        include_treats: includeTreats,
      })
      setResult(res)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not get meal suggestions")
    } finally {
      setBusy(false)
    }
  }

  const remaining = day?.remaining

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Eating out?</DialogTitle>
          <DialogDescription>
            Find one meal that fits your macros — not the whole day.
          </DialogDescription>
        </DialogHeader>

        {!result && (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              void submit()
            }}
          >
            {/* budget summary */}
            {remaining && !overrideOpen && (
              <div className="rounded-lg bg-mist px-4 py-3 text-sm">
                <p className="font-medium mb-1.5">Today's remaining</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground tabular-nums">
                  {remaining.kcal != null && (
                    <span>{formatKcal(Math.max(0, remaining.kcal))} kcal</span>
                  )}
                  {remaining.protein_g != null && (
                    <span>{formatGrams(Math.max(0, remaining.protein_g))} g protein</span>
                  )}
                  {remaining.carb_g != null && (
                    <span>{formatGrams(Math.max(0, remaining.carb_g))} g carbs</span>
                  )}
                  {remaining.fat_g != null && (
                    <span>{formatGrams(Math.max(0, remaining.fat_g))} g fat</span>
                  )}
                  {!remaining.kcal && !remaining.protein_g && !remaining.carb_g && !remaining.fat_g && (
                    <span>No targets set — set targets to use remaining macros.</span>
                  )}
                </div>
              </div>
            )}

            {/* per-meal controls */}
            <div className="rounded-lg border px-4 py-3 space-y-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Meal settings</p>
              <div className="flex flex-wrap items-center gap-3">
                <Stepper
                  label="Meals left today"
                  value={mealsLeft}
                  min={1}
                  max={10}
                  onChange={setMealsLeft}
                />
                {mealsLeft !== settingsMpd && (
                  <button
                    type="button"
                    disabled={savingMpd}
                    className="text-xs text-spinach underline underline-offset-2 disabled:opacity-60"
                    onClick={async () => {
                      setSavingMpd(true)
                      try {
                        await setMealsPerDay(mealsLeft)
                        setSettingsMpd(mealsLeft)
                      } catch {
                        // ignore — non-critical
                      } finally {
                        setSavingMpd(false)
                      }
                    }}
                  >
                    {savingMpd ? "Saving…" : "Save as default"}
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Checkbox
                  id="include-treats"
                  checked={includeTreats}
                  onCheckedChange={(checked) => setIncludeTreats(!!checked)}
                />
                <label
                  htmlFor="include-treats"
                  className="text-sm cursor-pointer select-none"
                >
                  Include a treat (dessert, shakes)
                </label>
              </div>
            </div>

            {/* restaurant picker */}
            <fieldset>
              <legend className="mb-2 text-sm font-medium">Restaurant / chain</legend>
              {chainsError ? (
                <p className="text-sm text-destructive">{chainsError}</p>
              ) : chains.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading chains…</p>
              ) : (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {chains.map((chain) => (
                    <button
                      key={chain}
                      type="button"
                      onClick={() => setRestaurant(chain)}
                      className={`flex items-center justify-between rounded-lg border px-3 py-3 text-sm text-left transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 ${
                        restaurant === chain
                          ? "border-spinach bg-spinach/10 font-medium"
                          : "hover:bg-mist"
                      }`}
                    >
                      <span className="min-w-0 truncate">{chain}</span>
                      {restaurant === chain && (
                        <Check className="ml-1 h-4 w-4 shrink-0 text-spinach" />
                      )}
                    </button>
                  ))}
                </div>
              )}
            </fieldset>

            {/* override budget toggle */}
            <button
              type="button"
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => setOverrideOpen((v) => !v)}
            >
              <ChevronDown
                className={`h-4 w-4 transition-transform ${overrideOpen ? "rotate-180" : ""}`}
              />
              {overrideOpen ? "Use today's remaining macros" : "Override budget"}
            </button>

            {overrideOpen && (
              <div className="grid grid-cols-2 gap-3">
                <Field id="fm-kcal" label="Calories">
                  <Input
                    id="fm-kcal"
                    inputMode="decimal"
                    placeholder="e.g. 600"
                    value={kcal}
                    onChange={(e) => setKcal(e.target.value)}
                  />
                </Field>
                <Field id="fm-protein" label="Protein (g)">
                  <Input
                    id="fm-protein"
                    inputMode="decimal"
                    placeholder="e.g. 40"
                    value={protein}
                    onChange={(e) => setProtein(e.target.value)}
                  />
                </Field>
                <Field id="fm-carbs" label="Carbs (g)">
                  <Input
                    id="fm-carbs"
                    inputMode="decimal"
                    placeholder="e.g. 60"
                    value={carbs}
                    onChange={(e) => setCarbs(e.target.value)}
                  />
                </Field>
                <Field id="fm-fat" label="Fat (g)">
                  <Input
                    id="fm-fat"
                    inputMode="decimal"
                    placeholder="e.g. 20"
                    value={fat}
                    onChange={(e) => setFat(e.target.value)}
                  />
                </Field>
              </div>
            )}

            {error && (
              <p className="flex items-center gap-1.5 text-sm text-destructive" role="alert">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {error}
              </p>
            )}

            <Button type="submit" disabled={busy || !restaurant} className="w-full py-6 text-base">
              {busy ? "Finding orders…" : "Find orders"}
            </Button>
          </form>
        )}

        {result && (
          <div className="space-y-4">
            {/* back button */}
            <button
              type="button"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => setResult(null)}
            >
              ← Back
            </button>

            <div>
              <h3 className="font-semibold">{result.restaurant}</h3>
              {/* per-meal target (what one meal should eat) */}
              {result.per_meal_target && (
                <div className="mt-1.5 rounded-lg bg-mist px-3 py-2 text-xs tabular-nums text-muted-foreground">
                  <span className="font-medium text-foreground">Per-meal target</span>
                  {" ("}1 of {result.meals_left} meal{result.meals_left !== 1 ? "s" : ""} left{")"}:{" "}
                  {result.per_meal_target.kcal != null && <span className="mr-2">{formatKcal(result.per_meal_target.kcal)} kcal</span>}
                  {result.per_meal_target.protein_g != null && <span className="mr-2">{formatGrams(result.per_meal_target.protein_g)} g protein</span>}
                  {result.per_meal_target.carb_g != null && <span className="mr-2">{formatGrams(result.per_meal_target.carb_g)} g carbs</span>}
                  {result.per_meal_target.fat_g != null && <span>{formatGrams(result.per_meal_target.fat_g)} g fat</span>}
                </div>
              )}
              {result.treats_excluded && (
                <p className="mt-1 text-xs text-muted-foreground">Desserts &amp; shakes excluded</p>
              )}
            </div>

            {result.note && (
              <div className="flex gap-2 rounded-lg bg-mist px-4 py-3 text-sm">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <p>{result.note}</p>
              </div>
            )}

            {result.orders.length === 0 && !result.note && (
              <p className="text-sm text-muted-foreground">
                No orders found that fit within that budget. Try increasing meals left or loosening the limits.
              </p>
            )}

            {result.orders.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Best orders
                </p>
                {result.orders.map((order) => (
                  <OrderCard key={order.rank} order={order} rank={order.rank} />
                ))}
              </div>
            )}

            {result.over_budget.length > 0 && (
              <details className="rounded-lg border">
                <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-muted-foreground">
                  {result.over_budget.length} item{result.over_budget.length !== 1 ? "s" : ""} that would blow your budget
                </summary>
                <ul className="divide-y border-t">
                  {result.over_budget.map((item, i) => (
                    <li key={i} className="px-4 py-3">
                      <p className="text-sm font-medium">{item.name}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">{item.portion}</p>
                      <p className="mt-1 text-xs text-roast">
                        Blows: {item.blows.map(blowLabel).join(", ")}
                      </p>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => setResult(null)}
            >
              Try another restaurant
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
