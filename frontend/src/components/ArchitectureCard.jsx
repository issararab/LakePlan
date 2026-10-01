import { useState } from 'react'
import { renderMarkdown } from '../utils/markdown'

export default function ArchitectureCard({
  content,
  onApprove,
  onRequestChanges,
  onInputToggle,   // called with true/false when change textarea opens/closes
  disabled,
  approvalState,   // null | 'approved' | 'changed'
}) {
  const [showChangeInput, setShowChangeInput] = useState(false)
  const [changeText, setChangeText] = useState('')

  function openChangeInput() {
    setShowChangeInput(true)
    if (onInputToggle) onInputToggle(true)
  }

  function closeChangeInput() {
    setShowChangeInput(false)
    setChangeText('')
    if (onInputToggle) onInputToggle(false)
  }

  function submitChanges() {
    const text = changeText.trim()
    if (!text) return
    closeChangeInput()
    onRequestChanges(text)
  }

  return (
    <div className="architecture-card">
      <div className="architecture-card-header">
        <span className="architecture-badge">Architecture Proposal</span>
      </div>

      <div
        className="architecture-card-body md"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
      />

      {!disabled && (
        <div className="architecture-card-actions">
          {!showChangeInput && (
            <button className="arch-btn arch-btn--approve" onClick={onApprove}>
              ✓ Approve architecture
            </button>
          )}

          <div className="arch-change-section">
            {showChangeInput ? (
              <>
                <textarea
                  className="arch-change-input"
                  placeholder="Describe what you'd like to change…"
                  value={changeText}
                  onChange={e => setChangeText(e.target.value)}
                  rows={3}
                  autoFocus
                />
                <div className="arch-change-buttons">
                  <button
                    className="arch-btn arch-btn--submit-change"
                    onClick={submitChanges}
                    disabled={!changeText.trim()}
                  >
                    Submit changes
                  </button>
                  <button className="arch-btn arch-btn--cancel" onClick={closeChangeInput}>
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <button className="arch-btn arch-btn--request-changes" onClick={openChangeInput}>
                ✎ Request changes
              </button>
            )}
          </div>
        </div>
      )}

      {approvalState === 'approved' && (
        <div className="architecture-card-approved">
          ✓ Architecture approved — pricing calculated below
        </div>
      )}
      {approvalState === 'changed' && (
        <div className="architecture-card-changed">
          ✎ Changes requested — see updated proposal below
        </div>
      )}
    </div>
  )
}
