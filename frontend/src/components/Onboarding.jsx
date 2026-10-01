import { useState } from 'react'

const DbIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <ellipse cx="12" cy="7" rx="8" ry="3"/>
    <path d="M4 7v4c0 1.66 3.58 3 8 3s8-1.34 8-3V7"/>
    <path d="M4 11v4c0 1.66 3.58 3 8 3s8-1.34 8-3v-4"/>
  </svg>
)

const CloudIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9z"/>
    <path d="m9 15 3-3 3 3M12 12v6"/>
  </svg>
)

const StreamIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="2 12 6 12 8 4 12 20 15 9 17 12 22 12"/>
  </svg>
)

const BrainIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="2.5"/>
    <circle cx="4.5" cy="7" r="1.5"/><circle cx="19.5" cy="7" r="1.5"/>
    <circle cx="4.5" cy="17" r="1.5"/><circle cx="19.5" cy="17" r="1.5"/>
    <path d="M6 7.8 9.5 10M14.5 10 18 7.8M6 16.2 9.5 14M14.5 14 18 16.2"/>
    <path d="M12 9.5v-6M12 14.5v6"/>
  </svg>
)

const SlidersIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="4" y1="6" x2="20" y2="6"/>
    <line x1="4" y1="12" x2="20" y2="12"/>
    <line x1="4" y1="18" x2="20" y2="18"/>
    <circle cx="8" cy="6" r="2" fill="white" stroke="currentColor"/>
    <circle cx="16" cy="12" r="2" fill="white" stroke="currentColor"/>
    <circle cx="10" cy="18" r="2" fill="white" stroke="currentColor"/>
  </svg>
)

const CATEGORIES = [
  {
    id: 'dwh-migration',
    color: '#2563eb',
    bg: '#eff6ff',
    label: 'Data Warehouse Migration',
    desc: 'Migrate from Snowflake, Redshift, Synapse, or other legacy platforms to Databricks Lakehouse',
    Icon: DbIcon,
  },
  {
    id: 'cloud-migration',
    color: '#0891b2',
    bg: '#ecfeff',
    label: 'Cloud Platform Migration',
    desc: 'Relocate existing workloads or data infrastructure to Databricks on a new cloud provider',
    Icon: CloudIcon,
  },
  {
    id: 'realtime',
    color: '#d97706',
    bg: '#fffbeb',
    label: 'Real-Time Use Case',
    desc: 'Design streaming pipelines, low-latency analytics, or event-driven processing on Databricks',
    Icon: StreamIcon,
  },
  {
    id: 'ml-genai',
    color: '#7c3aed',
    bg: '#f5f3ff',
    label: 'ML or GenAI Use Case',
    desc: 'Estimate costs for model training, fine-tuning, feature engineering, or generative AI workloads',
    Icon: BrainIcon,
  },
  {
    id: 'custom',
    color: '#374151',
    bg: '#f3f4f6',
    label: 'Custom Mode',
    desc: 'Define your own scenario and receive a tailored cost breakdown based on your specifications',
    Icon: SlidersIcon,
  },
]

export default function Onboarding({ onComplete, mode, onModeChange }) {
  const [step, setStep]               = useState('category')
  const [category, setCategory]       = useState(null)
  const [description, setDescription] = useState('')

  function selectCategory(cat) {
    setCategory(cat)
    setStep('describe')
  }

  function handleStart() {
    const text = description.trim()
    if (!text) return
    onComplete(category, text)
  }

  if (step === 'category') {
    return (
      <div className="onboarding">
        <div className="onboarding-hero">
          <div className="onboarding-top-row">
            <h1 className="onboarding-title">LakePlan</h1>
            <div className="mode-toggle" role="group" aria-label="Interface mode">
              <button
                className={`mode-toggle-btn${mode === 'user' ? ' mode-toggle-btn--active' : ''}`}
                onClick={() => onModeChange && onModeChange('user')}
              >
                User
              </button>
              <button
                className={`mode-toggle-btn${mode === 'debug' ? ' mode-toggle-btn--active' : ''}`}
                onClick={() => onModeChange && onModeChange('debug')}
              >
                Debug
              </button>
            </div>
          </div>
          <p className="onboarding-intro">
            I'm your Databricks pricing specialist, here to help you accurately model
            the total cost of your Databricks adoption across compute, storage, DBU rates,
            savings plans, and enterprise licensing.
          </p>
          <p className="onboarding-question">What are you looking to evaluate?</p>
        </div>

        <div className="category-grid">
          {CATEGORIES.map(cat => (
            <button
              key={cat.id}
              className="category-card"
              onClick={() => selectCategory(cat)}
              style={{ '--cat-color': cat.color }}
            >
              <div className="cat-icon-wrap" style={{ background: cat.bg, color: cat.color }}>
                <cat.Icon />
              </div>
              <div className="cat-text">
                <span className="cat-label">{cat.label}</span>
                <span className="cat-desc">{cat.desc}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="onboarding">
      <button className="back-link" onClick={() => setStep('category')}>← Back</button>

      <div className="onboarding-hero" style={{ marginTop: 8 }}>
        <div className="selected-badge" style={{ background: category.bg, color: category.color, borderColor: 'transparent' }}>
          <div style={{ width: 16, height: 16, color: category.color }}>
            <category.Icon />
          </div>
          {category.label}
        </div>
        <h2 className="onboarding-title" style={{ fontSize: 22 }}>
          What are you trying to solve?
        </h2>
        <p className="onboarding-intro">
          Provide context on your current environment, data volumes, team size, and
          any constraints or timelines. The more detail you share, the more precise
          the pricing estimates will be.
        </p>
      </div>

      <div className="describe-section">
        <textarea
          className="describe-input"
          placeholder={
            category.id === 'dwh-migration'
              ? "e.g. Migrating from Snowflake — approximately 80 TB of data, 30 analysts, daily batch reporting and ad hoc queries…"
              : category.id === 'ml-genai'
              ? "e.g. Fine-tuning a large language model on proprietary data, ~10B tokens, monthly retraining cycle, inference in production…"
              : "Describe your current environment, workload characteristics, data volumes, and expected usage patterns…"
          }
          value={description}
          onChange={e => setDescription(e.target.value)}
          rows={5}
          autoFocus
        />
        <button
          className="start-btn"
          onClick={handleStart}
          disabled={!description.trim()}
        >
          Start Conversation →
        </button>
      </div>
    </div>
  )
}
