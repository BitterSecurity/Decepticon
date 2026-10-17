# Web theme contract

## 1. Identity

Preserve the existing dense security dashboard, sidebar, cards, and dark terminal surfaces. The theme switch changes the dashboard palette without changing layout.

## 2. Color

`src/app/globals.css` is the canonical palette: `background`, `foreground`, `card`, `popover`, `primary`, `secondary`, `muted`, `accent`, `border`, `input`, `ring`, and `sidebar` CSS variables have light and dark values. Components use the corresponding Tailwind semantic utilities.

Status and severity indicators retain their existing Tailwind hue and translucent background. On light surfaces, text uses shade 800; `dark:` preserves the existing 300/400 shade. This covers red, orange, amber, yellow, lime, green, emerald, teal, cyan, blue, violet, purple, slate, and zinc. Fixed dark graph canvases, activity panels, command palette, and terminals retain their existing foreground colors.

## 3. Typography

Preserve Geist sans and Geist Mono, the existing `text-xs` metadata, `text-sm` controls/body, `text-base` card headings, and `text-2xl` page titles. Font weights remain those of the existing components.

## 4. Spacing and layout

Preserve Tailwind spacing and existing dashboard scroll ownership. The header remains `h-14` with `px-6`; the theme button remains `h-8 w-8` and uses the shared Button primitive.

## 5. Primitives and states

Use existing `components/ui` Button, Badge, Card, Table, Sheet, and Select primitives. Status badges keep their text labels as well as hue. The theme button has an accessible label, target-mode tooltip, keyboard focus ring, and a disabled pre-hydration placeholder. It toggles the resolved light/dark mode, including saved system preferences.

## 6. Motion

Keep existing control transitions. `ThemeProvider` disables transitions during a theme change to prevent intermediate mixed colors. No new animation is introduced.

## 7. Shape and depth

Preserve `--radius` and derived radius tokens, existing border opacity, translucent surfaces, and backdrop blur. The theme change adds no new elevation treatment.

## 8. Accessibility and scope

Status/severity text on light surfaces targets 4.5:1 contrast. Preserve dark-mode colors and keyboard semantics. Browser QA covers default dark, light, both persisted preferences, system-dark toggling, and hydration. Existing fixed-dark terminal/graph/command surfaces and unrelated responsive or performance redesigns are outside this theme correction.
