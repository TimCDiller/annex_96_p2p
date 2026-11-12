# Live classroom market

Students submit bids from their phones through a Google Form; the big screen
shows who has submitted and, when you press **Clear the market**, clears all
bids with the course market's own rule, draws the result and scores every
student. Each student plays a household from a **role card** shown in the
form: what they need, what they have to sell, and what that energy is worth
to them.

```
students' phones       ->  Google Form  ->  Google Sheet + Apps Script web app  ->  big_screen.html
(only the form link)                        (your Google account)                   (fetches every 5 s)
```

Students need nothing but the form link: no accounts, no shared network. It is
not cheat-proof (anyone with the link can submit under any name), which is fine
for a classroom demonstration.

## One-time setup (about 15 minutes)

1. **Create an empty Google Sheet** in your Google Drive.
2. In the sheet: **Extensions → Apps Script**. Delete the sample code, paste
   the whole of [`apps_script.gs`](apps_script.gs), and save.
   At the top: `CARDS` is the number of role cards (one per student, default
   10; 0 for none) and `DECK_CODE` picks their numbers. To allow more
   expensive bids, change `GRID_CENTS`.
3. Choose `setup` in the function list at the top and press **Run**. Google
   asks you to authorise the script for your account; accept.
   It creates the form and links it to the sheet: name and card number, one
   page per card, then five bids. The execution log that opens below the code
   shows the form link and the form's edit page, and a new **Card links**
   sheet holds one link per card that opens the form with that card chosen.
4. **Deploy → New deployment**, type **Web app**:
   - Execute as: **Me**
   - Who has access: **Anyone**

   Press **Deploy** and copy the **Web app URL** (it ends in `/exec`).
5. Open [`big_screen.html`](big_screen.html) in a browser on the computer
   that drives the big screen, paste the web app URL and press **Connect**.
   The status line should say *Connected*. The page remembers the URL.

Check the form once from your phone, signed out of Google: if it asks you
to sign in, your account is a school or company account that restricts forms
to the organisation. Open the form's edit page → **Settings → Responses** and
turn off **Restrict to users in …**.

## In class

1. Give each student their own card link from the **Card links** sheet
   (chat, e-mail, LMS), or show the form link, as a short link or a QR code
   (in the form editor: **Send → link → shorten URL**), and tell each student
   their card number.
2. Press **New round** on the big screen. From then on it counts only answers
   submitted after that moment, and only each name's latest answer, so
   students can correct a bid by submitting again.
3. While bids come in, the screen shows only *who* has submitted, not what
   they bid.
4. Press **Clear the market**: the price, the figure and a table of every bid
   and its result. Late bids still update the result live.
5. Discuss, then **New round** for the next one. Old answers stay in the
   sheet for later, but are no longer counted.

**Demo bids** fills the screen with ten made-up students holding cards, to
rehearse without the form.

### Background participants

The **Background** menu adds eight participants (A–H) whose bids clear
together with the students' bids: the same three markets as the
[market clearing explorer](../market_clearing_explorer.html).

| background | what the students are bidding into |
|---|---|
| empty | only their own bids |
| normal market | a mix of buyers and sellers; clears at 17¢ on its own |
| stable market | 40 small bids between 13¢ and 23¢; clears at 18¢, and single bids barely move it |
| volatile market | balanced blocks that tie from 3¢ to 27¢; clears at 13¢, and one kWh can move it to 6¢ or 20¢ |

On the big screen the background is hatched, greyer and lettered; students
are bright and numbered. The waiting screen names the background but does not
show its bids. The choice takes effect at once and is remembered, so pick it
before pressing **New round**. Ideas: let the class try to move the stable
market's price, or to calm the volatile one.

### Role cards and scores

Without a reason to bid, students guess. With `CARDS` set, the form deals
each student a household: an empty battery, a solar roof, a heat pump, an
electric car, … with energy to buy or sell, and private values, e.g. *"the
first 4 kWh are worth 30¢ to you (else from the grid), 2 more only 10¢ (the
price drops in two hours)"*. The student picks their card number on the first
page (or the card link has picked it), reads the card on the next page, then
bids. Hand out the numbers in order: the households alternate between buyers
and sellers, so any number of students gets a balanced market.

On the big screen:

- the waiting screen shows each name with its card number, and flags a card
  that two students chose, an invalid number, and cards nobody has used yet;
- after clearing, the table shows each student's household, **score** and the
  score they would have had **with true values**: the same market, but every
  card bidding exactly its values. The score is what the market gained the
  household: for a buyer, value minus price per kWh bought; for a seller,
  price minus the least it accepts. Buying more than it can use is worth
  nothing; selling more than it has means buying the rest from the grid.
- the summary adds the class score, the benchmark and the top score.

A new deck for the next class: change `DECK_CODE` (or `CARDS`), save, and run
`updateCards()`. The form keeps its link; the card pages and the **Card links**
sheet are rewritten, and the big screen picks the new cards up by itself.

[`role_cards.html`](role_cards.html) shows the same deck as printable cards
(four per A4 page) and an answer key, for a class on paper: set the same
number of students and deck code as in the script. Students who bid on paper
cards through a form without cards can start their name with the card number
(`7 Ada`); the big screen then scores them too.

Good questions for the discussion: who beat the benchmark by shading their
bid, and who lost a trade by shading too much? Did the class as a whole gain
more or less than with true values?

## Bids, as students enter them

- **Amount (kWh):** positive to buy, negative to sell (`4` buys up to 4 kWh,
  `-3` sells up to 3 kWh), the same sign convention as an agent's bid vector.
- **Price (cents):** 0 to the grid price. A buy counts at that price and every
  lower one, a sell at that price and every higher one.
- Up to five bids; unused ones stay empty. A participant whose bids overlap
  (a buy above one of its own sells) is netted, as in the course market.

## Good to know

- **Who can see the bids?** The web app URL returns every answer in the sheet
  to anyone who has it. Keep it on the big-screen computer, and let students
  use first names or team names.
- **Changing the script later:** **Deploy → Manage deployments → edit (pencil)
  → Version: New version → Deploy** keeps the same URL. A *new* deployment
  gets a new URL.
- **Upgrading from the version without cards:** paste the new script, run
  `setup()` once more (it makes a new form with cards; delete the old form or
  leave it, its answers are ignored), then publish a new version of the
  deployment as above, so the web app URL stays the same.
- **A fresh start:** delete the response rows in the sheet (keep the header
  row), or simply press **New round**.
- **Status says "Could not reach the web app":** the URL is wrong, or access
  is not set to *Anyone*. Opening the URL in a browser tab should show the
  answers as text.
