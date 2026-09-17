# 09 · Frontend

React 19 + Vite, React Query pour toutes les données, Recharts pour les
graphiques. Build servi par l'API (`frontend/dist`). Pas de routeur externe :
navigation par hash (`useHashRoute`), pages chargées en `lazy()`.

## Les 7 pages (depuis le 2026-09-17)

| Page | Rôle | Endpoints principaux |
|---|---|---|
| **Cockpit** (défaut) | « Est-ce que ça gagne, et est-ce sous contrôle ? » : KPI, compte vs SPY vs panier, positions × protection × décision LT, à décider, attribution de l'écart, shadow A/B, rebalance, santé système | `/performance/benchmark`, `/portfolio/protection`, `/system/health`, `/lt_decision`, `/performance/gap`, `/shadow`, `/rebalance/preview`, `/proposals` |
| Propositions | file du jour, approbation/rejet (bulk, overrides), historique de vetos | `/proposals*` |
| Portfolio | positions, journal, courbe d'equity, décisions LT, ajout/clôture manuels | `/portfolio`, `/equity_curve`, `/trade/*` |
| Univers | table scorée (piliers, badges, drift, sparklines), rebuild, backtest in-page | `/universe*`, `/backtest*` |
| Scoring Lab | edge du scoring sur la fenêtre live | `/scoring/lab` |
| Mon Portefeuille | book personnel long terme (indépendant du moteur) | `/my_portfolio*` |
| Préférences | thème, densité, token API, défauts des propositions | — |

Treize pages ont été retirées le 2026-09-17 (Briefing, Watchlist, Secteurs,
Ticker Detail, Comparer, Catalysts, News, Macro, Performance, Attribution,
Risk Monitor, Data Health, Audit). Leurs endpoints backend restent servis
(routines, tests) ; leurs composants sont dans l'historique git si besoin.

## Conventions

- `src/config/nav.js` : **source unique** des pages (sections, libellés,
  sous-titres). Ajouter une page = une entrée ici + `PAGES` dans `App.jsx`.
- `src/api/client.js` : un `fetchX` par endpoint (GET lève `ApiError`, POST
  renvoie `{ok, …}`). `src/hooks/useApi.js` : un hook React Query par
  fetch, avec `staleTime`/`refetchInterval` alignés sur les caches backend.
- Types : `src/api/types.ts` générés depuis OpenAPI (`npm run gen:types`) ;
  la CI échoue sur drift.
- Styles : tokens dans `styles/tokens.css` (thème clair/sombre, densité),
  primitives dans `styles/shell.css`, **`styles/cockpit.css`** pour les pages
  récentes (`.kpi`, `.pill--ok|warn|danger|info`, `.cockpit-card`,
  `.cockpit-table`, `.health-strip`). Objectif : zéro style inline dans les
  nouvelles pages.
- Accessibilité : skip-link, focus visible, `prefers-reduced-motion`,
  palette de commandes (Cmd+K) avec actions `goto-cockpit` / `goto-proposals`.
- Tests : Vitest sur `src/utils/__tests__` (58 tests) ; ESLint bloquant.

## Ajouter un indicateur au Cockpit

1. Endpoint dans `backend/routers/performance.py` (tag `cockpit`), test dans
   `backend/tests/test_cockpit_router.py`.
2. `fetchX` + `useX` (client/hooks).
3. Carte `<Card>` dans `CockpitPage.jsx` avec les classes de `cockpit.css`.
4. `npm run gen:types`, `npx eslint .`, `npx vite build`.
