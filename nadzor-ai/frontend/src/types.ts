export type Severity = 'critical' | 'major' | 'minor'

export interface Principal {
  subject: string
  full_name: string
  department: string
  position: string
  roles: string[]
  valid_until: string
  source: string
}

export interface Evidence {
  doc_id: string
  doc_title: string
  page: number
  bbox: [number, number, number, number]
  extracted: string
  role: string
}

export interface NormRef {
  doc: string
  clause: string
  edition: string
  actual: boolean
  title?: string
  text?: string
  edition_check?: { actual: boolean; message: string }
}

export interface Finding {
  id: string
  public_id: string
  object_id: string
  object_name?: string
  transition: string
  code: string
  severity: Severity
  confidence: number
  priority_score: number
  title: string
  structural_element: Record<string, string>
  evidence: Evidence[]
  norm_refs: NormRef[]
  norm_texts?: NormRef[]
  legal_refs: { act: string; article: string; part: string; title?: string }[]
  field_check: string
  documents_to_request: string[]
  verification: Record<string, unknown>
  injection_suspected: boolean
  detector: string
  provenance: Record<string, string>
  created_at: string
  assessment?: { verdict: string; comment: string; created_at: string } | null
}

export interface ObjectBrief {
  id: string
  name: string
  address: string
  district: string
  permit_number: string
  stage: string
  developer: string
  contractor: string
  planned_completion: string
  data_source: string
  assigned_to: string
  department: string
  protected: boolean
}

export interface DocumentBrief {
  id: string
  object_id: string
  title: string
  doc_kind: string
  state_kind: string
  revision: string
  doc_date: string | null
  page_count: number
  facts_count: number
  signature_status: string
  sha256: string
}

export interface AttentionItem {
  finding_id: string
  public_id: string
  priority_score: number
  severity: Severity
  confidence: number
  element: Record<string, string>
  location: string
  summary: string
  what_to_check: string
  documents_to_take: string[]
  norm_refs: NormRef[]
  checked: boolean
}
