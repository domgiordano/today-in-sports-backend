"""
The daily mail, rendered.

Email is not the web and this file is shaped by that. Layout is tables, because
Outlook still lays out with Word and has no flexbox or grid. Styles are inline,
because Gmail strips <style> from forwarded mail and several clients strip it
outright. The logo is a PNG, because Gmail refuses SVG entirely — the dot
matrix would simply not arrive.

Both parts are built together and always sent together. A text/plain
alternative is not a courtesy: mail with only an HTML part scores worse with
spam filters, and it is the part that screen readers and watches show.

Nothing here fetches anything. It takes what the caller already has and returns
a subject and two bodies, so it can be tested without a mailbox.
"""

from html import escape

BOARD = "#131a22"
ARENA = "#070b11"
SUNK = "#0d131a"
BORDER = "#232d3a"
AMBER = "#f5a524"
RED = "#d6212f"
CHALK = "#eef2f7"
DIM = "#8494a6"

SITE = "https://todayinsports.app"
LOGO = f"{SITE}/brand/wordmark-email.png"

# Kept in one place because email clients ignore a stylesheet and every one of
# these has to be pasted onto the element that uses it.
FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, "
        "Arial, sans-serif")
MONO = "'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"


def _tier_chip(tier):
    return (
        f'<span style="font-family:{MONO};font-size:11px;color:{AMBER};'
        f'border:1px solid {AMBER};border-radius:10px;padding:2px 7px;'
        f'white-space:nowrap;">Q{escape(str(tier))}</span>'
    )


def _question_row(question, index, *, show_answers):
    """One question, as a bordered block rather than a table row.

    Rows in a shared table would column-align prompts of wildly different
    lengths against each other, which reads as a spreadsheet of trivia rather
    than as five questions.
    """
    prompt = escape(str(question.get("prompt") or ""))
    sport = escape(str(question.get("sport") or "")).upper()
    source = question.get("sourceUrl") or question.get("sourceDatasetRef")

    answer = ""
    if show_answers:
        value = question.get("answer")
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        if value is not None:
            answer = (
                f'<div style="margin-top:8px;font-family:{FONT};font-size:13px;'
                f'color:{AMBER};">Answer: {escape(str(value))}</div>'
            )

    cite = ""
    if source:
        cite = (
            f'<div style="margin-top:8px;font-family:{FONT};font-size:12px;">'
            f'<a href="{escape(str(source))}" style="color:{DIM};'
            f'text-decoration:underline;">source</a></div>'
        )

    return f"""
      <tr>
        <td style="padding:0 0 10px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 border="0" style="background:{SUNK};border:1px solid {BORDER};
                 border-radius:6px;">
            <tr>
              <td style="padding:14px 16px;">
                <div style="margin-bottom:8px;">
                  {_tier_chip(question.get('tier', index + 1))}
                  <span style="font-family:{MONO};font-size:11px;color:{DIM};
                        letter-spacing:.06em;padding-left:8px;">{sport}</span>
                </div>
                <div style="font-family:{FONT};font-size:15px;line-height:1.45;
                     color:{CHALK};font-weight:600;">{prompt}</div>
                {answer}
                {cite}
              </td>
            </tr>
          </table>
        </td>
      </tr>"""


def _button(label, url):
    """A button drawn as a table, which is the only kind Outlook renders."""
    return f"""
      <table role="presentation" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td align="center" bgcolor="{AMBER}" style="border-radius:4px;">
            <a href="{escape(url)}" style="display:inline-block;padding:12px 26px;
               font-family:{FONT};font-size:14px;font-weight:700;
               letter-spacing:.04em;text-transform:uppercase;color:#1a1204;
               text-decoration:none;border-radius:4px;">{escape(label)}</a>
          </td>
        </tr>
      </table>"""


def daily_digest(quiz_date, questions, *, state="proposed",
                 admin_url=f"{SITE}/admin/schedule", reviewed=False):
    """
    The day's mail.

    `state` is what the caller is telling the reader about: "proposed" for a day
    still open to review, "published" for one that has gone out. The difference
    is not cosmetic — a proposed day is a request to act and says so in the
    subject, and a published one is a record.

    Answers travel only when the mail is a review request. A digest of the live
    quiz with the answers in it is a spoiler sitting in the inbox of anybody who
    might otherwise have played.
    """
    proposed = state == "proposed"
    show_answers = proposed
    count = len(questions)

    subject = (
        f"{count} question{'s' if count != 1 else ''} to review for {quiz_date}"
        if proposed else
        f"Today in Sports — {quiz_date} is live"
    )

    lede = (
        "These are the five proposed for that date. Approve them, or deny any "
        "one and another will be cycled in to take its place."
        if proposed else
        "These are the five that went out. Nothing to do — this is a record of "
        "what players are seeing."
    )

    # Shown in the inbox list beside the subject, and nowhere else.
    preheader = escape(f"{count} for {quiz_date}. {'Needs review.' if proposed else 'Published.'}")

    rows = "".join(
        _question_row(q, i, show_answers=show_answers)
        for i, q in enumerate(questions)
    )

    empty = (
        f'<tr><td style="padding:18px;font-family:{FONT};font-size:15px;'
        f'color:{RED};background:{SUNK};border:1px solid {BORDER};'
        f'border-radius:6px;">Nothing is assembled for {escape(str(quiz_date))} '
        f'yet. That day will have no quiz unless something is added.</td></tr>'
    )

    banner = ""
    if proposed and reviewed:
        banner = (
            f'<tr><td style="padding:0 0 14px 0;font-family:{FONT};font-size:13px;'
            f'color:{DIM};">Already reviewed — this is a copy for the record.</td></tr>'
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:{ARENA};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{ARENA};padding:24px 12px;">
  <tr>
    <td align="center">

      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="width:600px;max-width:600px;background:{BOARD};
             border:1px solid {BORDER};border-radius:10px;">
        <tr>
          <td align="center" style="padding:26px 24px 6px 24px;">
            <img src="{LOGO}" width="470" alt="Today in Sports"
                 style="display:block;width:470px;max-width:100%;height:auto;border:0;">
          </td>
        </tr>

        <tr>
          <td style="padding:14px 24px 0 24px;">
            <div style="font-family:{MONO};font-size:12px;color:{DIM};
                 letter-spacing:.1em;">{escape(str(quiz_date))}</div>
            <div style="height:3px;width:44px;background:{RED};margin:10px 0 14px 0;
                 border-radius:2px;font-size:0;line-height:0;">&nbsp;</div>
            <div style="font-family:{FONT};font-size:15px;line-height:1.55;
                 color:{DIM};">{escape(lede)}</div>
          </td>
        </tr>

        <tr>
          <td style="padding:18px 24px 0 24px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              {banner}
              {rows if questions else empty}
            </table>
          </td>
        </tr>

        <tr>
          <td align="center" style="padding:8px 24px 26px 24px;">
            {_button('Review the schedule' if proposed else 'Open the admin portal', admin_url)}
          </td>
        </tr>
      </table>

      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="width:600px;max-width:600px;">
        <tr>
          <td style="padding:16px 24px;font-family:{FONT};font-size:12px;
              line-height:1.6;color:{DIM};" align="center">
            You are getting this because you review questions for
            <a href="{SITE}" style="color:{DIM};">todayinsports.app</a>.
          </td>
        </tr>
      </table>

    </td>
  </tr>
</table>
</body>
</html>"""

    return {"subject": subject, "html": html, "text": _text(
        quiz_date, questions, lede=lede, admin_url=admin_url,
        show_answers=show_answers)}


def _text(quiz_date, questions, *, lede, admin_url, show_answers):
    """The plain part. Not a stripped copy — written to be read."""
    lines = [f"TODAY IN SPORTS — {quiz_date}", "", lede, ""]

    if not questions:
        lines.append(f"Nothing is assembled for {quiz_date} yet.")
    for i, q in enumerate(questions, start=1):
        lines.append(f"{i}. [Q{q.get('tier', i)} {str(q.get('sport') or '').upper()}] "
                     f"{q.get('prompt') or ''}")
        if show_answers and q.get("answer") is not None:
            value = q["answer"]
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            lines.append(f"   Answer: {value}")
        source = q.get("sourceUrl") or q.get("sourceDatasetRef")
        if source:
            lines.append(f"   Source: {source}")
        lines.append("")

    lines += [admin_url, "",
              "You are getting this because you review questions for "
              "todayinsports.app."]
    return "\n".join(lines)
