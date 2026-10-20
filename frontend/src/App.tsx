import { useEffect, useState } from 'react'
import { getHealth } from './api'
import Dashboard, { type ConnectionState } from './Dashboard'

function App() {
  const [connection, setConnection] = useState<ConnectionState>('checking')

  useEffect(() => {
    const controller = new AbortController()

    getHealth(controller.signal)
      .then(() => setConnection('connected'))
      .catch(() => {
        if (!controller.signal.aborted) {
          setConnection('disconnected')
        }
      })

    return () => controller.abort()
  }, [])

  return <Dashboard connection={connection} onConnectionChange={setConnection} />
}

export default App
