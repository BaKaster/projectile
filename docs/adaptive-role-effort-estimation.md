# Adaptive role and effort estimation

The work generator remains responsible for selecting stages and stable `work_code` values. The effort
estimator runs after it and enriches every selected work with role assignments, person-hours, an uncertainty
range, hourly rates, and financial totals.

## Estimation policy

1. A keyword profile selects an initial role mix and a calibration baseline.
2. Confirmed signals apply catalogued multipliers.
3. Numeric values from facts attached by the work generator scale the relevant work logarithmically and are
   capped, so a raw object count cannot create an unbounded estimate.
4. With `effort_mode=auto` and an OpenAI key, the model may refine role assignments and hours only for
   existing `work_code` and catalogued `role_code` values, and only inside the deterministic uncertainty range.
5. Rates and amounts are always applied and calculated by backend code. If model refinement fails, the API
   returns the deterministic estimate and a warning.

Baseline hours are priors for calibration, not claimed market averages. Production accuracy requires storing
actual hours by `work_code`, role, and scope drivers, then periodically recalibrating profile baselines and
multipliers.

## Reference basis

- NIST SP 800-115 separates penetration testing into planning, discovery, attack, and reporting. This supports
  treating security testing as a multi-part specialist task rather than a generic engineering item:
  https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-115.pdf
- SEI's COCOMO material models effort through size, scale factors, and effort multipliers, and explicitly calls
  for organizational calibration:
  https://insights.sei.cmu.edu/documents/883/2011_005_001_15419.pdf
- GitLab publishes small-task reference bands from under four hours through two days. They are used only as a
  coarse calibration scale, not as role-specific measured averages:
  https://handbook.gitlab.com/handbook/marketing/project-management-guidelines/issues/

The supplied MONS rate table is represented in `data/role-effort-catalog.json`; it is deliberately independent
of time norms.
