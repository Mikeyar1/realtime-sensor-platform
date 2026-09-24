/** Physiological card definitions shared by the three supported drills.
 * Behavioral definitions live in AAR.Standardized.specs, which renders them.
 */
window.LabReview = window.LabReview || {};

LabReview.DrillMetrics = {
  physiological: [
    { id: 'heart_rate', label: 'Heart Rate', unit: 'bpm', format: 'number',
      source: 'biosignal', field: 'hr.mean', size: 'md' },
    { id: 'hrv_rmssd', label: 'HRV (RMSSD)', unit: 'ms', format: 'ms',
      source: 'biosignal', field: 'hrv.rmssd_ms', size: 'md' },
    { id: 'pupil_diameter', label: 'Pupil Diameter', unit: 'mm', format: 'decimal',
      source: 'biosignal', field: 'pupil.mean_mm', size: 'md' },
  ],
};
