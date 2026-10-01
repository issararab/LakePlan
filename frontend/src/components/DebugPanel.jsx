import { useState } from 'react'

const STEP_META = {
  phase:      { color: '#7c3aed', bg: '#f5f3ff', icon: '◆' },
  llm:        { color: '#1d4ed8', bg: '#eff6ff', icon: '⚡' },
  extraction: { color: '#0369a1', bg: '#e0f2fe', icon: '⬡' },
  rule:       { color: '#0d9488', bg: '#f0fdfa', icon: '⚙' },
  transition: { color: '#7c3aed', bg: '#faf5ff', icon: '→' },
  plan:       { color: '#b45309', bg: '#fffbeb', icon: '▤' },
  sql:        { color: '#15803d', bg: '#f0fdf4', icon: '⬡' },
  error:      { color: '#dc2626', bg: '#fef2f2', icon: '✗' },
}

function DebugStep({ step }) {
  const [expanded, setExpanded] = useState(false)
  const meta = STEP_META[step.type] || STEP_META.llm
  const canExpand = Boolean(step.full_detail)

  return (
    <div
      className={`debug-step${canExpand ? ' debug-step--expandable' : ''}`}
      onClick={canExpand ? () => setExpanded(v => !v) : undefined}
    >
      <div className="debug-step-icon" style={{ background: meta.bg, color: meta.color }}>
        {meta.icon}
      </div>
      <div className="debug-step-content">
        <div className="debug-step-header">
          <span className="debug-step-label" style={{ color: meta.color }}>
            {step.label}
          </span>
          {canExpand && (
            <span className="debug-step-expand-hint">
              {expanded ? '▴ collapse' : '▾ expand'}
            </span>
          )}
        </div>
        {expanded && step.full_detail ? (
          <pre className="debug-step-full">{step.full_detail}</pre>
        ) : (
          <span className="debug-step-detail">{step.detail}</span>
        )}
      </div>
    </div>
  )
}

export default function DebugPanel({ steps }) {
  const [open, setOpen] = useState(true)

  if (!steps || steps.length === 0) return null

  return (
    <details className="debug-panel" open={open} onToggle={e => setOpen(e.target.open)}>
      <summary className="debug-panel-summary">
        <span className="debug-panel-toggle">{open ? '▾' : '▸'}</span>
        Agent steps
        <span className="debug-panel-count">{steps.length}</span>
      </summary>
      <div className="debug-panel-body">
        {steps.map((step, i) => <DebugStep key={i} step={step} />)}
      </div>
    </details>
  )
}
