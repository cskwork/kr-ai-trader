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
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>오늘의 저널 {data?.date ? `— ${data.date}` : ''}</h3>
          <div className="spacer" />
          <button
            className="primary"
            onClick={() => setTick((t) => t + 1)}
            style={{ padding: '6px 16px', fontSize: 13 }}
          >
            새로고침
          </button>
        </div>
        {err ? (
          <div className="empty" style={{ color: 'var(--red)' }}>{err}</div>
        ) : !data ? (
          <div className="empty">불러오는 중…</div>
        ) : !data.exists ? (
          <div className="empty">
            오늘 기록이 아직 없어요.{' '}
            <b>분석 실행</b> 탭에서 사이클을 돌리면{' '}
            <code>journal/{data.date}.md</code>에 AI 의사결정이 쌓여요.
          </div>
        ) : (
          <pre className="md">{data.markdown}</pre>
        )}
      </div>
    </div>
  )
}
