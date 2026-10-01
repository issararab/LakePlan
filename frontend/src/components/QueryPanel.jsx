import { escapeHtml } from '../utils/markdown'

export default function QueryPanel({ name, sql, rows }) {
  const label = name ? name.replace(/_/g, ' ') : 'query'
  const count = (rows || []).length

  return (
    <details className="query-panel" open>
      <summary>{label} — {count} row{count !== 1 ? 's' : ''}</summary>
      <div className="query-panel-inner">
        <div className="query-label">SQL</div>
        <pre className="query-sql">{sql}</pre>

        {rows && rows.length > 0 && (
          <>
            <div className="query-label">Results</div>
            <div className="query-rows-wrap">
              <table>
                <thead>
                  <tr>
                    {Object.keys(rows[0]).map(col => (
                      <th key={col}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={i}>
                      {Object.values(row).map((val, j) => (
                        <td key={j}>{val ?? ''}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </details>
  )
}
