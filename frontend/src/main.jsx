import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.jsx'
// L'ordre des imports CSS est important pour la cascade :
//   1. index.css (incluant tokens.css via @import) → legacy + variables
//   2. styles/shell.css → primitives modernes (override legacy en cas
//      de collision : .nav-item, .btn, transitions, etc.)
import './index.css'
import './styles/shell.css'

// En prod, on neutralise log/debug/info/warn/trace mais on garde console.error
// pour que les erreurs non gérées restent visibles dans la DevTools.
if (import.meta.env.PROD) {
  const noop = () => {};
  ['log', 'debug', 'info', 'warn', 'trace'].forEach(k => { console[k] = noop; });
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
