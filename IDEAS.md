# RiftLab — Feature Ideas Backlog

Older, roughly prioritized feature backlog (from the lol-helper days). The research roadmap lives in the README.

---

## Next up

### SQLite persistence
Store every match fetch in a local DB so rank and stats are tracked over time without re-hitting the API.
- Snapshot rank (LP, tier) on each run with a timestamp
- Skip already-fetched match IDs to avoid redundant API calls
- Foundation for all trend-based features below

### Session diff
On each run, compare current rank vs last saved snapshot and print:
`+/- X LP  |  +Y games this session  |  WR today: Z%`
Makes every run feel like a debrief rather than a cold lookup.

### Per-champ trend plots
Plot Cassiopeia/Syndra WR% and KDA over rolling 20-game windows using matplotlib.
Shows whether you're improving or tilting on a specific champion over time.

---

## Coaching & analysis

### Death timing analysis
Break down deaths by game phase (0-15 early / 15-25 mid / 25+ late).
Main account dies 9/game — understanding *when* is more actionable than just the count.

### CC conversion rate
For support games: ratio of CC score to kill participation.
High CC + low KP = team not following up. Low CC + high KP = getting picks with vision/positioning instead.

### Roam timing (support/jungle)
Flag games where the support left lane before 5 min and whether the ADC died while they were gone.
Direct feedback on the "roam timing vs ADC survival" problem.

### Win condition fingerprint
For each champion: what stats correlate with wins vs losses on that specific champ?
e.g. "When you play Bard and die 6+, you win 20%. When you die ≤4, you win 65%."

---

## Quality of life

### `--watch` mode
Re-run the stats CLI (`riftlab.analysis.stats`) every N minutes and print a diff, useful during a grind session.

### champion pool suggestions refresh
Expand CHAMP_DB in `analysis/playstyle.py` with more champions and patch-aware tier data.

### Export to CSV
`python -m riftlab.analysis.stats --export` dumps match rows to a CSV for external analysis (spreadsheets, etc).
