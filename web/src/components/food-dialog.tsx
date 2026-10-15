import { useEffect, useState } from "react"

import { addFood, ApiError } from "@/api"
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

function optionalNumber(value: string) {
  if (value.trim() === "") return null
  const number = Number(value)
  return Number.isFinite(number) ? number : undefined
}

export function FoodDialog({
  open,
  onOpenChange,
  initialName,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialName: string
  onCreated: (foodId: number) => void
}) {
  const [name, setName] = useState("")
  const [store, setStore] = useState("")
  const [brand, setBrand] = useState("")
  const [kcal, setKcal] = useState("")
  const [protein, setProtein] = useState("")
  const [carbs, setCarbs] = useState("")
  const [fat, setFat] = useState("")
  const [fiber, setFiber] = useState("")
  const [portionLabel, setPortionLabel] = useState("")
  const [portionGrams, setPortionGrams] = useState("")
  const [perPortion, setPerPortion] = useState(true)
  const [alias, setAlias] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setName(initialName)
    setAlias(initialName)
    setStore("")
    setBrand("")
    setKcal("")
    setProtein("")
    setCarbs("")
    setFat("")
    setFiber("")
    setPortionLabel("")
    setPortionGrams("")
    setPerPortion(true)
    setError(null)
  }, [open, initialName])

  async function save() {
    const macros = {
      kcal: optionalNumber(kcal),
      protein_g: optionalNumber(protein),
      carb_g: optionalNumber(carbs),
      fat_g: optionalNumber(fat),
    }
    if (!name.trim() || Object.values(macros).some((value) => value == null)) {
      setError("Enter a name, calories, protein, carbs, and fat.")
      return
    }
    const fiberValue = optionalNumber(fiber)
    if (fiberValue === undefined) {
      setError("Fiber needs a number.")
      return
    }
    const grams = optionalNumber(portionGrams)
    if (grams === undefined) {
      setError("Portion grams needs a number.")
      return
    }
    if ((portionLabel.trim() && grams == null) || (!portionLabel.trim() && grams != null)) {
      setError("A portion needs both a name and grams.")
      return
    }
    if (perPortion && grams == null) {
      setError("Enter the portion grams these numbers belong to.")
      return
    }
    setBusy(true)
    setError(null)
    try {
      const created = await addFood({
        name: name.trim(),
        kcal: macros.kcal as number,
        protein_g: macros.protein_g as number,
        carb_g: macros.carb_g as number,
        fat_g: macros.fat_g as number,
        fiber_g: fiberValue,
        brand: brand.trim() || null,
        store: store.trim() || null,
        portion_label: portionLabel.trim() || null,
        portion_grams: grams,
        macros_are_per_portion: perPortion,
        alias: alias.trim() || null,
      })
      onOpenChange(false)
      onCreated(created.food_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add that food")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100svh-2rem)] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New food</DialogTitle>
          <DialogDescription>
            Use the label when you have it. Numbers are per 100 g unless they belong to one portion.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <Field id="food-name" label="Name">
            <Input id="food-name" value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="food-store" label="Store">
              <Input id="food-store" value={store} onChange={(event) => setStore(event.target.value)} />
            </Field>
            <Field id="food-brand" label="Brand">
              <Input id="food-brand" value={brand} onChange={(event) => setBrand(event.target.value)} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field id="food-kcal" label="Calories">
              <Input id="food-kcal" inputMode="decimal" value={kcal} onChange={(event) => setKcal(event.target.value)} />
            </Field>
            <Field id="food-protein" label="Protein (g)">
              <Input id="food-protein" inputMode="decimal" value={protein} onChange={(event) => setProtein(event.target.value)} />
            </Field>
            <Field id="food-carbs" label="Carbs (g)">
              <Input id="food-carbs" inputMode="decimal" value={carbs} onChange={(event) => setCarbs(event.target.value)} />
            </Field>
            <Field id="food-fat" label="Fat (g)">
              <Input id="food-fat" inputMode="decimal" value={fat} onChange={(event) => setFat(event.target.value)} />
            </Field>
          </div>
          <Field id="food-fiber" label="Fiber (g)">
            <Input id="food-fiber" inputMode="decimal" value={fiber} onChange={(event) => setFiber(event.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="food-portion" label="Portion">
              <Input id="food-portion" value={portionLabel} placeholder="1 burger" onChange={(event) => setPortionLabel(event.target.value)} />
            </Field>
            <Field id="food-portion-grams" label="Portion grams">
              <Input id="food-portion-grams" inputMode="decimal" value={portionGrams} onChange={(event) => setPortionGrams(event.target.value)} />
            </Field>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox id="per-portion" checked={perPortion} onCheckedChange={setPerPortion} />
            <label htmlFor="per-portion" className="text-sm">
              These numbers are for one portion
            </label>
          </div>
          <Field id="food-alias" label="Also answer to">
            <Input id="food-alias" value={alias} onChange={(event) => setAlias(event.target.value)} />
          </Field>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div>
            <Button type="submit" disabled={busy}>
              {busy ? "Saving" : "Save food"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
