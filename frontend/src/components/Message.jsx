import { renderMarkdown } from '../utils/markdown'
import QueryPanel from './QueryPanel'
import DebugPanel from './DebugPanel'

export default function Message({ msg, mode }) {
  if (msg.role === 'user') {
    return (
      <div className="message message--user">
        <div className="avatar user-avatar">You</div>
        <div className="bubble-wrap">
          <div className="bubble user-bubble">{msg.text}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="message message--agent">
      <div className="avatar agent-avatar">AI</div>
      <div className="bubble-wrap">
        {mode === 'debug' && <DebugPanel steps={msg.debugSteps} />}
        <div
          className="bubble agent-bubble md"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.answer || msg.text || '') }}
        />
        {mode === 'debug' && (msg.queryResults || []).map((r, i) => (
          <QueryPanel key={i} name={r.name} sql={r.sql} rows={r.rows} />
        ))}
      </div>
    </div>
  )
}
