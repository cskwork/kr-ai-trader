import { useEffect, useState } from 'react'
import { getJournalToday } from '../api'

export default function JournalView() {
  const [data, setData] = useState<{ date: string; markdown: string; exists: boolean } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let mounted = true
    getJournalToday()
      .then((d) => mounted && setData(d))
      .catch((e: unknown) => mounted && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      mounted = false
    }
  }, [tick])

  return (
    <div>
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>
            Journal {data?.date ?? ''}
          </h3>
          <div className="spacer" />
          <button className="primary" onClick={() => setTick((t) => t + 1)}>
            Refresh
          </button>
        </div>
        {err ? (
          <div className="empty" style={{ color: '#ff7b72' }}>{err}</div>
        ) : !data ? (
          <div className="empty">loading…</div>
        ) : !data.exists ? (
          <div className="empty">
            아직 오늘 자 저널이 없습니다. Run cycle 탭에서 사이클을 실행하면 의사결정 기록이
            <code> journal/{data.date}.md</code> 에 쌓입니다.
          </div>
        ) : (
          <pre className="md">{data.markdown}</pre>
        )}
      </div>
    </div>
  )
}
