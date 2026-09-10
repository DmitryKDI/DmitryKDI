import assert from 'node:assert/strict'
import test from 'node:test'

import { coverageAllowsAnalysis } from '../src/documentCoverage.ts'

test('unknown coverage never allows analysis', () => {
  assert.equal(coverageAllowsAnalysis(undefined), false)
})

test('document ready status cannot override incomplete page coverage', () => {
  assert.equal(coverageAllowsAnalysis({ ingestion_complete: false }), false)
})

test('explicit complete server coverage allows analysis', () => {
  assert.equal(coverageAllowsAnalysis({ ingestion_complete: true }), true)
})
