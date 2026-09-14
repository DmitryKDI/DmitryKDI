import type { BackendCoverageReport } from './backendApi'

/** Запуск допустим только по явному серверному свидетельству полного покрытия. */
export function coverageAllowsAnalysis(
  report: Pick<BackendCoverageReport, 'ingestion_complete'> | undefined,
): boolean {
  return report?.ingestion_complete === true
}
