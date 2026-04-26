import { BarChart3 } from "lucide-react"
import { Button } from "@/components/ui/button"

/**
 * Монтаж: оставьте <div id="vendra-ui-root"> в шаблоне Django и подключите
 * собранный /static/frontend/assets/*.js (см. frontend/README.md).
 */
export default function App() {
  return (
    <div className="mx-auto flex min-h-svh max-w-3xl flex-col items-center justify-center gap-8 p-8">
      <div className="rounded-2xl border border-border/80 bg-card p-10 shadow-soft">
        <p className="text-sm font-medium text-muted-foreground">Vendra design stack</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">Tailwind + shadcn + Lucide</h1>
        <p className="mt-2 max-w-md text-sm text-muted-foreground">
          Добавляйте компоненты: <code className="rounded bg-muted px-1 py-0.5">npx shadcn@latest add [name]</code> из
          каталога <code className="rounded bg-muted px-1 py-0.5">frontend/</code>.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Button type="button">
            <BarChart3 className="size-4" />
            Primary
          </Button>
          <Button type="button" variant="secondary">
            Secondary
          </Button>
          <Button type="button" variant="outline">
            Outline
          </Button>
        </div>
      </div>
    </div>
  )
}
