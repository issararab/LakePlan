export function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function buildTable(lines) {
  const rows = lines.filter(l => !l.match(/^\|[-| :]+\|$/))
  if (!rows.length) return lines.join('\n')
  const [header, ...body] = rows
  const ths = header.split('|').filter(c => c.trim()).map(c => `<th>${c.trim()}</th>`).join('')
  const trs = body.map(r =>
    `<tr>${r.split('|').filter(c => c.trim()).map(c => `<td>${c.trim()}</td>`).join('')}</tr>`
  ).join('')
  return `<div class="table-wrap"><table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div>`
}

function renderLists(html) {
  const lines = html.split('\n')
  const out = []
  let inList = false
  let items = []
  for (const line of lines) {
    if (/^- .+/.test(line.trim())) {
      inList = true
      items.push(line.trim().replace(/^- /, ''))
    } else {
      if (inList) {
        out.push('<ul>' + items.map(item => `<li>${item}</li>`).join('') + '</ul>')
        items = []
        inList = false
      }
      out.push(line)
    }
  }
  if (inList) out.push('<ul>' + items.map(item => `<li>${item}</li>`).join('') + '</ul>')
  return out.join('\n')
}

function isTableRow(line) {
  const t = line.trim()
  if (!t.startsWith('|') || !t.endsWith('|')) return false
  // require at least one non-empty cell — filters out bare | and |   | lines
  return t.split('|').some(c => c.trim().length > 0)
}

function renderTables(html) {
  const lines = html.split('\n')
  const out = []
  let inTable = false
  let tableLines = []
  for (const line of lines) {
    if (isTableRow(line)) {
      inTable = true
      tableLines.push(line.trim())
    } else {
      if (inTable) { out.push(buildTable(tableLines)); tableLines = []; inTable = false }
      out.push(line)
    }
  }
  if (inTable) out.push(buildTable(tableLines))
  return out.join('\n')
}

export function renderMarkdown(text) {
  let html = escapeHtml(text)

  const codeBlocks = []
  html = html.replace(/```(?:[a-z]*)?\n?([\s\S]*?)```/g, (_, code) => {
    const i = codeBlocks.length
    codeBlocks.push(`<pre class="code-block"><code>${code.trim()}</code></pre>`)
    return `\x00CODE${i}\x00`
  })

  html = html.replace(/^---+$/mg, '<hr>')
  html = html.replace(/^#### (.+)$/mg, '<h4>$1</h4>')
  html = html.replace(/^### (.+)$/mg,  '<h3>$1</h3>')
  html = html.replace(/^## (.+)$/mg,   '<h2>$1</h2>')
  html = html.replace(/^# (.+)$/mg,    '<h1>$1</h1>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/`([^`]+)`/g,    '<code>$1</code>')
  html = renderTables(html)
  html = renderLists(html)
  html = html.replace(/\n/g, '<br>')
  // Strip <br> tags immediately before or after <hr> — blank lines around --- create excess space
  html = html.replace(/(<br>)+(<hr>)/g, '$2')
  html = html.replace(/(<hr>)(<br>)+/g, '$1')
  // Collapse any run of 2+ consecutive <br> to a single one — prevents double-blank-line gaps
  html = html.replace(/(<br>){2,}/g, '<br>')
  html = html.replace(/\x00CODE(\d+)\x00/g, (_, i) => codeBlocks[+i])

  return html
}
