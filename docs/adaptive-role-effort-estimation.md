# Adaptive role and effort estimation

The work generator remains responsible for selecting stages and stable `work_code` values. The effort
estimator runs after it and enriches every selected work with role assignments, person-hours, an uncertainty
range, hourly rates, and financial totals.

## Estimation policy

1. A keyword profile creates a safe fallback role mix and a calibration prior.
2. Confirmed signals apply catalogued multipliers to that fallback.
3. Only explicit capacity quantities attached to the relevant work scale fallback effort logarithmically: managed
   services/components, systems, sites, assets, configurations, or request/RFC volume. Durations and quality
   targets such as `15 minutes`, `4 hours`, `99.9%`, RTO/RPO, and telemetry cardinality do not scale recurring
   capacity. This prevents an SLA or a count of metrics from being mistaken for headcount.
4. With `effort_mode=auto` and an authenticated Codex CLI, the model selects supported `work_code` values,
   assigns one or more allowed roles, estimates person-hours and uncertainty ranges, and identifies whether
   work is one-time or monthly. It must provide document evidence; the stages in the result are inferred from
   the approved works rather than forced by the catalogue template. Project-specific works created during fact
   extraction participate in the same validation.
5. The backend rejects unknown work or role codes, duplicate assignments, inconsistent hour totals, and plans
   inconsistent hour totals, and empty resulting plans. It applies role rates and calculates all monetary amounts.
6. If the source explicitly states a contract term, the plan exposes separate one-time, monthly, and
   contract-total hours and amounts. Legacy `total_*` fields remain the unexpanded sum for compatibility.
7. If AI planning or validation fails, the API
   returns the deterministic estimate and a warning.

For an existing service with a confirmed change, the plan is deliberately mixed: `implement_point_change`
is a one-time block and service-operation works are monthly blocks. A recurring change pool is included only
when the source explicitly confirms a monthly RFC stream. Point integration does not imply discovery,
architecture, security review, deployment, or a full implementation lifecycle.

## Regression policy

Accuracy is measured only against references with the same horizon, currency, and cost perimeter. Supplier
quotes, licenses, hardware, missing role families, and mixed commercial totals remain useful coverage cases,
but are marked non-comparable and excluded from accuracy percentages. Monthly, one-time, and annual values
must be compared separately; the generated workbook total may legitimately combine them over a contract term.

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
