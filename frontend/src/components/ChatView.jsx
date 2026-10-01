import { useState, useRef, useEffect } from 'react'
import Message from './Message'
import Onboarding from './Onboarding'
import ArchitectureCard from './ArchitectureCard'
import DebugPanel from './DebugPanel'
import DatabricksLogo from './DatabricksLogo'

const SPINNING_WORDS = [
  'Thinking','Computing','Calculating','Generating','Processing',
  'Crafting','Working','Orchestrating','Synthesizing','Brewing',
]

function randomWord(exclude) {
  let word
  do { word = SPINNING_WORDS[Math.floor(Math.random() * SPINNING_WORDS.length)] }
  while (word === exclude && SPINNING_WORDS.length > 1)
  return word
}

function getSessionId() {
  let id = sessionStorage.getItem('pricing_session_id')
  if (!id) {
    id = crypto.randomUUID()
    sessionStorage.setItem('pricing_session_id', id)
  }
  return id
}

export default function ChatView({ mode, onModeChange }) {
  const [onboarded, setOnboarded]         = useState(false)
  const [messages, setMessages]           = useState([])
  const [input, setInput]                 = useState('')
  const [loading, setLoading]             = useState(false)
  const [loadingWord, setLoadingWord]     = useState(() => randomWord(null))
  const [approvedArchIds, setApprovedArchIds] = useState(new Set())
  const [changedArchIds, setChangedArchIds]   = useState(new Set())
  // true while an arch card's change-request textarea is open
  const [archChangeOpen, setArchChangeOpen]   = useState(false)
  const bottomRef      = useRef(null)
  const textareaRef    = useRef(null)
  const sessionId      = useRef(getSessionId())
  const abortCtrlRef   = useRef(null)

  // Cancel any in-flight request on unmount
  useEffect(() => () => abortCtrlRef.current?.abort(), [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Close any open arch change input when a new message arrives
  useEffect(() => {
    if (loading) setArchChangeOpen(false)
  }, [loading])

  // Cycle the spinning word every 2 s while loading
  useEffect(() => {
    if (!loading) return
    setLoadingWord(w => randomWord(w))
    const id = setInterval(() => setLoadingWord(w => randomWord(w)), 5000)
    return () => clearInterval(id)
  }, [loading])

  async function sendMessage(text, extraBody = {}) {
    setMessages(prev => [...prev, { role: 'user', text }])
    setLoading(true)

    abortCtrlRef.current?.abort()
    const controller = new AbortController()
    abortCtrlRef.current = controller

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId.current, mode, ...extraBody }),
        signal: controller.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        setMessages(prev => [...prev, { role: 'agent', text: `Error ${res.status}: ${err.detail || res.statusText}`, response_type: 'message' }])
      } else {
        const data = await res.json()
        setMessages(prev => [...prev, {
          role: 'agent',
          answer: data.answer,
          response_type: data.response_type || 'message',
          phase: data.phase,
          queryResults: data.query_results || [],
          debugSteps: data.debug_steps || [],
          id: crypto.randomUUID(),
        }])
      }
    } catch (err) {
      if (err.name === 'AbortError') return  // cancelled by newChat — silently discard
      setMessages(prev => [...prev, { role: 'agent', text: `Network error: ${err.message}`, response_type: 'message' }])
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }

  function handleOnboardingComplete(category, description) {
    setOnboarded(true)
    const firstMessage = `I'm evaluating Databricks for a ${category.label} use case.\n\n${description}`
    sendMessage(firstMessage, { path_id: category.id, path_label: category.label })
  }

  function handleApproveArchitecture(msgId) {
    setApprovedArchIds(prev => new Set([...prev, msgId]))
    sendMessage('Yes, the architecture looks correct. Please proceed to pricing.')
  }

  function handleRequestChanges(msgId, changeText) {
    setChangedArchIds(prev => new Set([...prev, msgId]))
    sendMessage(changeText)
  }

  async function submit() {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    await sendMessage(text)
  }

  async function newChat() {
    // Cancel any in-flight request immediately so the typing indicator disappears
    abortCtrlRef.current?.abort()
    abortCtrlRef.current = null
    setLoading(false)

    await fetch('/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId.current }),
    })
    setMessages([])
    setInput('')
    setOnboarded(false)
    setApprovedArchIds(new Set())
    setChangedArchIds(new Set())
    setArchChangeOpen(false)
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function onTextareaChange(e) {
    setInput(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }

  function renderMessage(msg, i) {
    if (msg.role === 'user') {
      return <Message key={i} msg={msg} mode={mode} />
    }

    if (msg.response_type === 'architecture_proposal') {
      const isApproved = approvedArchIds.has(msg.id)
      const isChanged  = changedArchIds.has(msg.id)
      const isActed    = isApproved || isChanged
      return (
        <div key={i} className="message message--agent">
          <div className="avatar agent-avatar">AI</div>
          <div className="bubble-wrap">
            {mode === 'debug' && <DebugPanel steps={msg.debugSteps} />}
            <ArchitectureCard
              content={msg.answer}
              disabled={isActed || loading}
              approvalState={isApproved ? 'approved' : isChanged ? 'changed' : null}
              onApprove={() => handleApproveArchitecture(msg.id)}
              onRequestChanges={text => handleRequestChanges(msg.id, text)}
              onInputToggle={isOpen => setArchChangeOpen(isOpen)}
            />
          </div>
        </div>
      )
    }

    return <Message key={i} msg={msg} mode={mode} />
  }

  // Hide input bar when the latest agent message is an unanswered architecture proposal
  const lastAgentMsg = [...messages].reverse().find(m => m.role === 'agent')
  const awaitingApproval = lastAgentMsg?.response_type === 'architecture_proposal'
    && !approvedArchIds.has(lastAgentMsg?.id)
    && !changedArchIds.has(lastAgentMsg?.id)

  return (
    <div className="chat-layout">
      <header className="chat-header">
        <DatabricksLogo size={30} withBackground />
        <span className="header-title">LakePlan</span>
        <div className="header-spacer" />
        <div className="mode-label">
          <span className={`mode-dot mode-dot--${mode}`} />
          {mode === 'user' ? 'User mode' : 'Debug mode'}
        </div>
        <button className="header-btn" onClick={newChat}>+ New chat</button>
      </header>

      <div className="messages-area">
        {!onboarded ? (
          <Onboarding onComplete={handleOnboardingComplete} mode={mode} onModeChange={onModeChange} />
        ) : (
          messages.map((msg, i) => renderMessage(msg, i))
        )}

        {loading && (
          <div className="message message--agent">
            <div className="avatar agent-avatar">AI</div>
            <div className="bubble-wrap">
              <div className="bubble typing-bubble">
                <div className="dot" /><div className="dot" /><div className="dot" />
                <span className="spinning-word">{loadingWord}...</span>
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Hide the input bar while awaiting architecture approval or while change textarea is open */}
      {onboarded && !archChangeOpen && !awaitingApproval && (
        <div className="input-bar">
          <form className="input-form" onSubmit={e => { e.preventDefault(); submit() }}>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={onTextareaChange}
              onKeyDown={onKeyDown}
              placeholder="Ask a follow-up question…"
              rows={1}
              disabled={loading}
            />
            <button className="send-btn" type="submit" disabled={loading || !input.trim()}>
              ➤
            </button>
          </form>
        </div>
      )}
    </div>
  )
}
