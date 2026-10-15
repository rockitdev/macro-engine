import { useEffect, useState } from "react"

import { ApiError, setTargets } from "@/api"
import { Field } from "@/components/field"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { formatLong } from "@/lib/format"
import type { Targets } from "@/types"

function required(value: string) {
  if (value.trim() === "") return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function TargetsDialog({
  open,
  onOpenChange,
  date,
  targets,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  date: string
  targets: Targets | null
  onSaved: () => void
}) {
  const [kcal, setKcal] = useState("")
  const [protein, setProtein] = useState("")
  const [carbs, setCarbs] = useState("")
  const [fat, setFat] = useState("")
  const [fiber, setFiber] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setKcal(targets ? String(targets.kcal) : "")
    setProtein(targets ? String(targets.protein_g) : "")
    setCarbs(targets ? String(targets.carb_g) : "")
    setFat(targets ? String(targets.fat_g) : "")
    setFiber(targets?.fiber_g == null ? "" : String(targets.fiber_g))
    setError(null)
  }, [open, targets])

  async function save() {
    const values = {
      kcal: required(kcal),
      protein_g: required(protein),
      carb_g: required(carbs),
      fat_g: required(fat),
    }
    if (Object.values(values).some((value) => value == null)) {
      setError("Enter calories, protein, carbs, and fat.")
      return
    }
    const fiberValue = fiber.trim() === "" ? null : required(fiber)
    if (fiber.trim() !== "" && fiberValue == null) {
      setError("Fiber needs a number.")
      return
    }
    setBusy(true)
    setError(null)
    try {
      await setTargets({
        kcal: values.kcal as number,
        protein_g: values.protein_g as number,
        carb_g: values.carb_g as number,
        fat_g: values.fat_g as number,
        fiber_g: fiberValue,
        effective_date: date,
      })
      onOpenChange(false)
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save targets")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Daily targets</DialogTitle>
          <DialogDescription>These apply from {formatLong(date)} onward.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <Field id="target-kcal" label="Calories">
            <Input id="target-kcal" inputMode="decimal" value={kcal} onChange={(event) => setKcal(event.target.value)} />
          </Field>
          <Field id="target-protein" label="Protein (g)">
            <Input id="target-protein" inputMode="decimal" value={protein} onChange={(event) => setProtein(event.target.value)} />
          </Field>
          <Field id="target-carbs" label="Carbs (g)">
            <Input id="target-carbs" inputMode="decimal" value={carbs} onChange={(event) => setCarbs(event.target.value)} />
          </Field>
          <Field id="target-fat" label="Fat (g)">
            <Input id="target-fat" inputMode="decimal" value={fat} onChange={(event) => setFat(event.target.value)} />
          </Field>
          <Field id="target-fiber" label="Fiber (g)">
            <Input id="target-fiber" inputMode="decimal" value={fiber} onChange={(event) => setFiber(event.target.value)} />
          </Field>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div>
            <Button type="submit" disabled={busy}>
              {busy ? "Saving" : "Save targets"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
