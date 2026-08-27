# Plan: Question quality

**Status**: Draft
**Created**: 2026-08-27
**Repo**: `today-in-sports-backend`

---

## Summary

The assembler work fixed *which* questions get picked. This is about what they
say. Four defects, all measured against the live bank of 25,941 approved
questions, all originating in the templates.

---

## The defects

### D1. The prompt gives away the answer — 954 questions

13% of numeric questions state a scoreline and then ask for something derivable
from it. These are subtraction tests wearing a sports question's clothes.

```
On April 7, 2018, the Philadelphia Phillies routed the Miami Marlins 20-1.
What was the margin of victory?
```

Sources: `mlb_templates.py:258` (blowout margin), `winter_templates.py:480`
(NBA blowout margin).

The tension is real: without the score the question is unanswerable, with it
the question is arithmetic. Both templates ask the wrong thing. **Fix: state one
side's score and ask for the other.** Not derivable, still a closest-guess
question, and "close" scores — which is the whole point of asking for a number.

### D2. Database notation in player-facing text — 1,331 questions

```
...thrashed FC Metz in the French Ligue 1 2021/22.
```

`2021/22` is how a database labels a season, not how anyone says it, and it is
redundant beside a prompt that already reads "On March 20, 2022". Strip the
trailing season from the competition name at render time.

### D3. No purchase on the answer

```
Liverpool FC and AFC Bournemouth produced a remarkable English Premier League
match on August 27, 2022. How many goals were scored in total?
```

"Remarkable" tells the player nothing. There is no route to the answer except
having memorised that specific fixture, so it plays as a blind guess inside the
tolerance. Same shape in `soccer_big_win` and `soccer_goal_fest`.

This is the one with a genuine judgment call in it — see the open question.

### D4. 204 prompt shapes across 25,941 questions

The six most common shapes cover 46% of the bank:

| Count | Shape |
|---:|---|
| 5,681 | `Who is this? Every clue you take is worth fewer points.` |
| 1,569 | `On this day in ####, the NAME sent NAME to which team?` |
| 1,544 | `On this day in ####, NAME signed with which team?` |
| 1,323 | `On this day in ####, the NAME traded which player to the NAME?` |
| 934 | `NAME ##, ####: the NAME acquired NAME from which club?` |
| 933 | `On NAME ##, ####, which future star made his first appearance...` |

Each template emits one sentence skeleton forever, so by day three the game
reads as the same five sentences with the nouns swapped. **Fix: 2–4 phrasings
per template, selected deterministically from the questionId** so a given
question always reads the same way while the bank as a whole varies.

### D5. 18 of 44 live quizzes contain no map question

Maps are 14% of served questions and absent from 41% of days, which is why they
read as missing. The assembler has no opinion about them. A soft preference for
including one when the date offers one, without making it a requirement.

---

## Open question

D3 has two honest answers and they lead to different games:

1. **Anchor the prompt** — give the player something to reason from ("the
   highest-scoring match of that Premier League season"). Requires a fact the
   detector does not currently compute, so it means work in the detectors as
   well as the templates.
2. **Convert to multiple choice** — four plausible totals, answerable by
   reasoning rather than recall. Cheap, and it makes the question winnable, but
   it pushes the bank further toward multiple choice, which is already 30% of
   served questions and part of why the game feels predictable.

---

## Out of scope

- New sports. NFL was the gap and is now closed; college football, tennis and
  golf remain unbuilt and are a content-pipeline effort, not a template one.
- The clue ladder's 5,681 identical prompts. It is a distinct format with its
  own screen, and "Who is this?" is arguably correct there — but it is the
  single largest shape in the bank and worth revisiting once D4 lands.
