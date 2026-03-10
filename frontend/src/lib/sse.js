export async function consumeSSE(response, onEvent) {
  if (!response?.ok) {
    throw new Error(`Request failed with status ${response?.status}`)
  }

  if (!response.body) {
    throw new Error('Streaming response body is missing')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (!line.startsWith('data:')) continue
      const payload = line.slice(5).trim()
      if (!payload) continue

      let event
      try {
        event = JSON.parse(payload)
      } catch {
        // Ignore malformed events to keep stream alive.
        continue
      }
      onEvent(event)
    }
  }

  const tail = buffer.trim()
  if (tail.startsWith('data:')) {
    const payload = tail.slice(5).trim()
    if (payload) {
      let event
      try {
        event = JSON.parse(payload)
      } catch {
        // Ignore malformed final event.
        return
      }
      onEvent(event)
    }
  }
}
