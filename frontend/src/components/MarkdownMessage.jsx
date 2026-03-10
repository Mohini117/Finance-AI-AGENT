import React from 'react'

function parseInline(text, keyPrefix = '') {
  const parts = []
  const regex = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g
  let last = 0
  let match
  let idx = 0

  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(<span key={`${keyPrefix}-t-${idx++}`}>{text.slice(last, match.index)}</span>)
    }

    if (match[1]) {
      parts.push(
        <code
          key={`${keyPrefix}-c-${idx++}`}
          className="rounded-md border border-gray-700 bg-gray-950 px-1.5 py-0.5 font-mono text-[11px] text-indigo-300"
        >
          {match[1].slice(1, -1)}
        </code>
      )
    } else if (match[2]) {
      parts.push(
        <strong key={`${keyPrefix}-b-${idx++}`} className="font-semibold text-white">
          {match[2].slice(2, -2)}
        </strong>
      )
    } else if (match[3]) {
      parts.push(
        <em key={`${keyPrefix}-i-${idx++}`} className="italic text-gray-200">
          {match[3].slice(1, -1)}
        </em>
      )
    }

    last = match.index + match[0].length
  }

  if (last < text.length) {
    parts.push(<span key={`${keyPrefix}-t-${idx++}`}>{text.slice(last)}</span>)
  }

  return parts
}

export default function MarkdownMessage({ content, streaming = false }) {
  if (!content) return null

  const lines = content.split('\n')
  const elements = []

  for (let i = 0; i < lines.length; i += 1) {
    const trimmed = lines[i].trim()
    const key = `line-${i}`

    if (!trimmed) {
      elements.push(<div key={key} className="h-3" />)
      continue
    }

    if (trimmed.startsWith('### ')) {
      elements.push(
        <h4 key={key} className="mt-2 text-xs font-semibold uppercase tracking-wide text-indigo-300">
          {parseInline(trimmed.slice(4), key)}
        </h4>
      )
      continue
    }

    if (trimmed.startsWith('## ')) {
      elements.push(
        <h3 key={key} className="mt-3 text-sm font-semibold text-white">
          {parseInline(trimmed.slice(3), key)}
        </h3>
      )
      continue
    }

    if (trimmed.startsWith('# ')) {
      elements.push(
        <h2 key={key} className="mt-4 border-b border-gray-700/60 pb-1 text-[15px] font-bold text-white">
          {parseInline(trimmed.slice(2), key)}
        </h2>
      )
      continue
    }

    if (trimmed === '---' || trimmed === '***' || trimmed === '___') {
      elements.push(<hr key={key} className="my-3 border-gray-700/60" />)
      continue
    }

    if (/^[-*•]\s/.test(trimmed)) {
      const items = []
      let j = i
      while (j < lines.length && /^[-*•]\s/.test(lines[j].trim())) {
        const text = lines[j].trim().replace(/^[-*•]\s/, '')
        items.push(
          <li key={`ul-${j}`} className="flex items-start gap-2 text-sm leading-7 text-gray-100">
            <span className="mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-indigo-400" />
            <span>{parseInline(text, `ul-${j}`)}</span>
          </li>
        )
        j += 1
      }
      elements.push(
        <ul key={`ul-wrap-${i}`} className="my-1 space-y-1">
          {items}
        </ul>
      )
      i = j - 1
      continue
    }

    if (/^\d+[.)]\s/.test(trimmed)) {
      const items = []
      let j = i
      while (j < lines.length && /^\d+[.)]\s/.test(lines[j].trim())) {
        const line = lines[j].trim()
        const number = line.match(/^(\d+)[.)]\s*/)?.[1] || `${j + 1}`
        const text = line.replace(/^\d+[.)]\s*/, '')
        items.push(
          <li key={`ol-${j}`} className="flex gap-2 text-sm leading-7 text-gray-100">
            <span className="w-5 flex-shrink-0 text-right font-semibold text-indigo-300">{number}.</span>
            <span>{parseInline(text, `ol-${j}`)}</span>
          </li>
        )
        j += 1
      }
      elements.push(
        <ol key={`ol-wrap-${i}`} className="my-1 space-y-1">
          {items}
        </ol>
      )
      i = j - 1
      continue
    }

    elements.push(
      <p key={key} className="text-sm leading-7 text-gray-100">
        {parseInline(trimmed, key)}
      </p>
    )
  }

  return (
    <div className="break-words">
      {elements}
      {streaming && (
        <span className="ml-1 inline-block h-[14px] w-[3px] animate-pulse rounded-sm bg-indigo-400 align-text-bottom" />
      )}
    </div>
  )
}
