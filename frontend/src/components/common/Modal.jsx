// Modal — primitive de dialog modal centralisée.
//
// Centralise les patterns dupliqués dans TickerAnalysisModal, ProposalsPage,
// UniverseManagerPage : backdrop, ESC pour fermer, scroll-lock body, ARIA
// dialog/aria-modal, focus-trap léger (1er focusable au mount).
//
// API minimaliste — on ne réinvente pas Radix, juste assez pour réduire la
// duplication et harmoniser le look-and-feel.
//
// Usage :
//   <Modal open={open} onClose={...} title="..." size="lg">
//     <div>...</div>
//   </Modal>

import { useEffect, useRef } from 'react';

const SIZES = {
  sm: 420,
  md: 640,
  lg: 880,
  xl: 1120,
  full: '92vw',
};

export default function Modal({
  open,
  onClose,
  title,
  size = 'md',
  children,
  footer,
  closeOnBackdrop = true,
  ariaLabel,
}) {
  const dialogRef = useRef(null);
  const lastFocusedRef = useRef(null);

  // ESC + scroll-lock + focus-trap léger.
  useEffect(() => {
    if (!open) return undefined;
    lastFocusedRef.current = document.activeElement;

    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose?.();
      }
    };
    document.addEventListener('keydown', onKey);

    // Verrouille le scroll body — overflow + padding-right pour compenser
    // la barre de scroll qui disparaît (évite le "saut").
    const prevOverflow = document.body.style.overflow;
    const scrollBarW = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = 'hidden';
    if (scrollBarW > 0) document.body.style.paddingRight = `${scrollBarW}px`;

    // Auto-focus le premier élément focusable du dialog.
    const t = setTimeout(() => {
      const root = dialogRef.current;
      if (!root) return;
      const focusable = root.querySelector(
        'button:not([disabled]), [href], input:not([disabled]), ' +
        'select:not([disabled]), textarea:not([disabled]), ' +
        '[tabindex]:not([tabindex="-1"])',
      );
      if (focusable) focusable.focus();
      else root.focus();
    }, 0);

    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      document.body.style.paddingRight = '';
      clearTimeout(t);
      // Restaure le focus sur l'élément qui a ouvert le modal.
      const last = lastFocusedRef.current;
      if (last && typeof last.focus === 'function') {
        try { last.focus(); } catch { /* déjà démonté */ }
      }
    };
  }, [open, onClose]);

  if (!open) return null;

  const width = SIZES[size] ?? SIZES.md;

  return (
    <div
      className="modal-backdrop"
      onClick={closeOnBackdrop ? onClose : undefined}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="modal-dialog"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel || title}
        tabIndex={-1}
        style={{ maxWidth: width }}
      >
        {title && (
          <div className="modal-header">
            <h2 className="modal-title">{title}</h2>
            <button
              type="button"
              onClick={onClose}
              className="modal-close"
              aria-label="Fermer le dialog"
            >×</button>
          </div>
        )}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}
