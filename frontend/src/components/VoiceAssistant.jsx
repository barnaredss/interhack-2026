import { useState, useRef } from 'react'
import { API_BASE } from '../config'
const STATES = { IDLE: 'idle', LISTENING: 'listening', PROCESSING: 'processing', SPEAKING: 'speaking' }

function buildContext(route, deliveryStatus, canToggle) {
  if (!route || !deliveryStatus) return 'No route loaded.'
  const firstPending = deliveryStatus.findIndex((s) => s === 'pending')
  const delivered = deliveryStatus.filter((s) => s === 'delivered').length
  const nextStop = firstPending >= 0 ? route.points[firstPending] : null
  const timeWindow = firstPending >= 0 ? route.windows?.[firstPending] : null
  const serviceTime = firstPending >= 0 ? route.service_times?.[firstPending] : null
  const canDeliver = firstPending >= 0 && canToggle(firstPending)

  return [
    `Truck: ${route.truck_id}`,
    `Progress: ${delivered} of ${route.points.length} stops delivered`,
    nextStop ? `Next stop (#${firstPending + 1}): ${nextStop.address}` : 'All stops completed',
    timeWindow ? `Delivery window: ${timeWindow.start} – ${timeWindow.end}` : '',
    serviceTime != null ? `Expected service time: ${serviceTime} min` : '',
    `Can mark next stop as delivered: ${canDeliver ? 'yes' : 'no — complete previous stops first'}`,
  ].filter(Boolean).join('\n')
}

async function askClaude(transcript, context) {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript, context })
  })
  if (!res.ok) throw new Error('Backend chat error')
  return res.json()
}

export default function VoiceAssistant({ route, deliveryStatus, canToggle, onMarkDelivered }) {
  const [status, setStatus] = useState(STATES.IDLE)
  const [transcript, setTranscript] = useState('')
  const [reply, setReply] = useState('')
  const recognitionRef = useRef(null)

  async function speak(text) {
    setStatus(STATES.SPEAKING)
    try {
      const res = await fetch(`${API_BASE}/api/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      })
      if (!res.ok) throw new Error('Backend tts error')

      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      const done = () => { URL.revokeObjectURL(url); setStatus(STATES.IDLE) }
      audio.onended = done
      audio.onerror = done
      audio.play()
    } catch {
      // Fallback to browser TTS if ElevenLabs fails
      speechSynthesis.cancel()
      setTimeout(() => {
        const utterance = new SpeechSynthesisUtterance(text)
        utterance.lang = navigator.language
        const reset = () => setStatus(STATES.IDLE)
        utterance.onend = reset
        utterance.onerror = reset
        speechSynthesis.speak(utterance)
      }, 100)
    }
  }

  async function handleTranscript(text) {
    setTranscript(text)
    setStatus(STATES.PROCESSING)

    try {
      const result = await askClaude(text, buildContext(route, deliveryStatus, canToggle))
      setReply(result.response)

      const firstPending = deliveryStatus?.findIndex((s) => s === 'pending') ?? -1

      if (result.action === 'mark_delivered' && firstPending >= 0) {
        onMarkDelivered(firstPending)
      } else if (result.action === 'navigate' && firstPending >= 0) {
        const p = route.points[firstPending]
        window.open(
          `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lng}&travelmode=driving`,
          '_blank'
        )
      }

      speak(result.response)
    } catch {
      const fallback = 'Sorry, something went wrong. Please try again.'
      setReply(fallback)
      speak(fallback)
    }
  }

  function startListening() {
    if (status !== STATES.IDLE) return
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      speak('Speech recognition is not supported in this browser.')
      return
    }

    const rec = new SpeechRecognition()
    rec.lang = navigator.language || 'es-ES'
    rec.interimResults = false
    rec.maxAlternatives = 1
    recognitionRef.current = rec

    rec.onstart = () => setStatus(STATES.LISTENING)
    rec.onresult = (e) => handleTranscript(e.results[0][0].transcript)
    rec.onerror = () => setStatus(STATES.IDLE)
    rec.onend = () => { if (status === STATES.LISTENING) setStatus(STATES.IDLE) }

    setTranscript('')
    setReply('')
    rec.start()
  }

  const isActive = status !== STATES.IDLE

  return (
    <div className="voice-assistant">
      {isActive && (
        <div className="voice-bubble">
          {transcript && <div className="voice-transcript">"{transcript}"</div>}
          {status === STATES.PROCESSING && (
            <div className="voice-processing">
              <span className="vp-dot" /><span className="vp-dot" /><span className="vp-dot" />
            </div>
          )}
          {reply && status === STATES.SPEAKING && (
            <div className="voice-reply">{reply}</div>
          )}
        </div>
      )}
      <button
        className={`voice-btn ${status}`}
        onClick={startListening}
        disabled={status !== STATES.IDLE}
        title="Tap to speak"
        aria-label="Voice assistant"
      >
        {status === STATES.IDLE && <MicIcon />}
        {status === STATES.LISTENING && <span className="voice-rec">REC</span>}
        {status === STATES.PROCESSING && <span className="voice-spin">⟳</span>}
        {status === STATES.SPEAKING && <span>♪</span>}
      </button>
    </div>
  )
}

function MicIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  )
}
