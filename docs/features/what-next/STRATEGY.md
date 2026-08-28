# What's left, and what would actually make it stick

**Created**: 2026-08-27
**Status**: for discussion, nothing built

---

## The number that reframes the question

```
today-in-sports-users : 1 row
today-in-sports-plays : 25 rows, 1 distinct player
```

One user. Twenty-five plays, all from the same person, across seven days.

That is you. The app has no audience yet, so **retention is not the binding
constraint and question quality is not either** — both are downstream of a
problem neither of them touches. Nobody has bounced off this game because the
opener was predictable. Nobody has bounced off it at all.

This matters because "how do we make it stick" has two very different answers
depending on which problem you are actually solving, and the work looks nothing
alike:

| If the problem is | The work is |
|---|---|
| People try it and do not come back | Question quality, streaks, notifications, groups |
| Nobody tries it | Distribution — one channel, deliberately |

Today it is the second. The quiz is good enough to test that with; it was
arguably not two days ago, and that is what this session was for.

**This is the thing I would push back on.** Every option below is worth doing
eventually. None of them will move a number that is currently one.

---

## The one real signal in the play data

Small sample, one person, so read it as a hint rather than a finding:

```
 0 answered / abandoned : 7      <- opened, left without answering anything
 1-4 answered / abandoned : 8
 5 answered / finished  : 10
```

Seven sessions of twenty-five ended before a single answer. If that ratio holds
with real players it is the most expensive number in the funnel, and it is
about the first screen rather than the questions behind it. Worth instrumenting
properly before optimising anything else.

---

## Should the Big 4 be the focus?

The instinct is right for a US audience. The data says it is not the change it
sounds like.

**Feasible:** the big 4 alone could fill 364 of 366 dates.

**But it makes the real problem worse.** The bank is 80% baseball today, and of
big-4 questions alone it is 87%. Cutting soccer and F1 does not rebalance
anything — it removes the two sports currently doing the most to stop every
quiz being about baseball. Soccer is the second-largest bank and covers 306
dates; F1 covers 169 and is the only sport that reaches February and March
properly.

The asymmetry is a source artefact, not a judgement about the sports:

| Sport | Questions | Dates covered | Archive reaches back to |
|---|---|---|---|
| MLB | 20,485 | 366 | 1871 (Retrosheet) |
| Soccer | 1,434 | 306 | 1990s |
| NFL | 1,193 | 155 | 1999 (nflverse) |
| NHL | 1,034 | 107 | 1990s |
| NBA | 805 | 229 | 1990s |
| F1 | 534 | 169 | 1950 (f1db) |

**NHL covering 107 of 366 dates is the number to care about.** A third of the
calendar. NFL at 155 is seasonal and honest — football does not happen in June
— but hockey does, and 107 says the source is thin rather than the sport is.

So: yes to the big 4 as a *priority*, no to it as a *cut*. Deepen NBA, NHL and
NFL; leave soccer and F1 alone until the big 4 can carry the calendar without
baseball taking three slots in five.

---

## Question ideas, ranked by what the data already supports

These need no new source. Everything named is a field already on the events.

**1. "Which of these happened on this day?"** — four events, one from the real
date and three from adjacent dates. Cross-sport by construction, so it is the
only format that gets *better* as the bank gets more lopsided. Uses events as
they stand.

**2. Same day, different year.** Two real events on the same calendar date,
decades apart: which came first? The corpus is date-anchored, so every date
already has these sitting in it. Cheap, and it makes the date the point rather
than the backdrop.

**3. Career arc.** Debut and finale are both detected already
(`player_debut`, `player_finale`). "This player debuted in 1954 and played his
last game in 1976 — who?" is a clue ladder with a spine, and it uses two events
about one person that currently produce two unrelated questions.

**4. The franchise-name questions.** `nba_franchises` already resolves what a
club was called in a given year, and it is currently used defensively — to
avoid getting names wrong. It is also a question: "In 1953 they were the
Milwaukee Hawks. What are they now?"

**5. Streaks and records** — needs aggregation the detectors do not do today,
so it is the first idea on this list with real cost. Worth it later, not first.

What I would not build: anything needing images, video, or a live feed. The
whole architecture is "immutable archives, deterministic templates, no model
consulted", and that property is why the questions are trustworthy.

---

## What is actually left in the code

Small, and none of it urgent:

- 10 of 43 quizzes still repeat a sport-and-format pairing, on dates thin
  outside baseball. Content depth, not a rule.
- 176 questions flagged for human review in the queue.
- The clue ladder's rungs are varied; its *shape* is still one format doing a
  lot of work at 5,681 questions.

---

## What I would do next, in order

1. **Instrument the funnel properly.** Seven abandons at zero answers is either
   the most important number here or noise, and right now there is no way to
   tell which.
2. **Pick one distribution channel and try it.** One is not a small audience,
   it is no audience, and every other decision is guesswork until that changes.
3. **Deepen NHL and NBA** — the calendar gaps, not the sport list.
4. **Build ideas 1 and 2 above.** Both are cross-sport by construction, so they
   improve variety without needing the bank to rebalance first.
