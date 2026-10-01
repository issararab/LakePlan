import { useState } from 'react'
import ChatView from './components/ChatView'

export default function App() {
  // Mode (user/debug) is now a header toggle, not a gating screen.
  // It persists in sessionStorage but defaults to 'user'.
  const [mode, setMode] = useState(
    () => sessionStorage.getItem('pricing_mode') || 'user'
  )

  function handleModeChange(newMode) {
    sessionStorage.setItem('pricing_mode', newMode)
    setMode(newMode)
  }

  return (
    <ChatView
      mode={mode}
      onModeChange={handleModeChange}
    />
  )
}
