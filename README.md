# today-in-sports-backend

Lambdas, the content pipeline, and the notability rule library for **Today in
Sports** — a daily five-question sports history quiz anchored to the calendar
date.

Plan: `~/Code/docs/features/today-in-sports/PLAN.md`
Research: `~/Code/docs/features/today-in-sports/SPIKE-FINDINGS.md`

## The core idea

Questions are generated from **real rows in real datasets by deterministic
rule**. No model is ever asked what happened — not at request time, and not
offline either. Notability, the one thing that looks like it needs judgment, is
computed:

```python
# A no-hitter is not an editorial opinion.
if team["hits"] == 0 and innings >= 9:
    ...
```

Scanning all 13 MLB games played on 1991-05-01 with that single rule returns
exactly one event — Nolan Ryan's seventh no-hitter — and nothing on a control
date. That is the whole architecture in one example.

An earlier design harvested Wikipedia prose instead. It was measured and
discarded: 22.8% of events could be pinned to an exact date, covering 95 of 366
calendar dates. See `SPIKE-FINDINGS.md`.

## Layout

```
lambdas/
  common/
    logger.py errors.py utility_helpers.py     ported from xomify-backend
    dynamo_helpers.py admin.py ssm_helpers.py
    sources/      one adapter per upstream dataset
    notability/   detectors — the rules that decide what matters
    templates/    question generation from detected events
scripts/          local, offline: ingest, detect, generate, verify
tests/            one test file per lambda, plus the detector regression suite
```

## Rules worth knowing before changing anything here

**Fire rate is the notability proxy.** A detector firing more than roughly
10–15 times per season is measuring something that merely happens, not something
worth asking about. Plain 1-0 games fire 59 times a season; restricting to
extra-innings 1-0 games brings it into band. Check the rate before adding a
detector.

**Attribution requires proof, not inference.** A no-hitter can be thrown by
several pitchers, and "the winning pitcher was on the no-hitting side" is true
for combined no-hitters too. Crediting an individual requires the no-hitting
team to have used exactly one pitcher. 1991 alone contains two combined
no-hitters that the naive check gets wrong.

**Use `officialDate`, never the queried date.** An MLB night game at 23:05Z on
10 October has an `officialDate` of 11 October and is returned under both dates.
Keying on the query date silently misfiles a large share of evening games onto
the previous day — fatal for a product built on "what happened on this date".

**Every question carries provenance.** `sourceUrl` and `sourceDatasetRef` are
mandatory and validation rejects a question without them. A null that reaches a
prompt ("the Detroit Stars routed the None") is a factual defect, not a cosmetic
one, and validation fails it too.

**The Negro Leagues are first-class.** MLB's official records include them and
`sportId=1` returns those games. They carry their real league name — Negro
National League (I), Eastern Colored League — never flattened to "MLB".

## Tests

```bash
./run_tests.sh              # everything
python3 -m pytest tests/test_notability_mlb.py -q
```

The detector regression suite runs against recorded fixtures in
`tests/fixtures/` and a `conftest.py` autouse fixture hard-fails any test that
attempts a live HTTP call. Cases are real events checkable against the
historical record: Ryan 1991-05-01, Martínez 1991-07-28, Larsen 1956-10-08,
the 1991 World Series Game 7, the combined no-hitters on 1991-07-13 and
1991-09-11, and a negative control on 1991-06-11.

## Attribution

Production ingestion uses Retrosheet, whose licence requires this notice to
appear prominently wherever the data is used:

> The information used here was obtained free of charge from and is copyrighted
> by Retrosheet. Interested parties may contact Retrosheet at
> "www.retrosheet.org".
