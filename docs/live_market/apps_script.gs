/**
 * Live classroom market: collects bids with a Google Form and serves them to big_screen.html.
 *
 * Paste this whole file into Extensions > Apps Script of an EMPTY Google Sheet, then:
 *   1. run setup() once -- it creates the form and links its answers to this sheet;
 *   2. Deploy > New deployment > Web app, execute as "Me", access "Anyone";
 *   3. give students the form link (in the execution log), and paste the web app URL
 *      into big_screen.html.
 * Full instructions: docs/live_market/README.md.
 */

const GRID_CENTS = 30;   // the grid price: the highest price anyone may bid, in cents
const MAX_BIDS = 5;      // bids per student

function setup() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet();
  const form = FormApp.create("P2P market: submit your bid");
  form.setDescription(
    "One hour of the peer-to-peer market. Enter up to " + MAX_BIDS + " bids.\n\n" +
    "Amount in kWh: positive to BUY, negative to SELL (4 = buy up to 4 kWh, -3 = sell up to 3 kWh).\n" +
    "Price in cents, 0 to " + GRID_CENTS + ": a buy counts at that price and every lower price, " +
    "a sell at that price and every higher price.\n\n" +
    "Leave unused bids empty. You can submit again: only your latest answer counts.");
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());
  form.setCollectEmail(false);
  form.setShowLinkToRespondAgain(true);
  form.setConfirmationMessage("Bid received. Watch the big screen!");
  try {
    form.setRequireLogin(false);   // school/company Google accounts restrict forms to the domain by default
  } catch (e) {
    // personal Google accounts have no such setting
  }

  form.addTextItem().setTitle("Your name or team").setRequired(true)
    .setHelpText("Shown on the big screen.");
  for (let i = 1; i <= MAX_BIDS; i++) {
    form.addTextItem().setTitle("Bid " + i + ": amount (kWh)")
      .setHelpText(i === 1 ? "+ to buy, - to sell, e.g. 4 or -3" : "")
      .setValidation(FormApp.createTextValidation().requireNumber()
        .setHelpText("A number: positive to buy, negative to sell.").build());
    form.addTextItem().setTitle("Bid " + i + ": price (cents)")
      .setValidation(FormApp.createTextValidation().requireNumberBetween(0, GRID_CENTS)
        .setHelpText("A price from 0 to " + GRID_CENTS + " cents.").build());
  }

  Logger.log("Form for students:  " + form.getPublishedUrl());
  Logger.log("Edit the form:      " + form.getEditUrl());
  Logger.log("Next: Deploy > New deployment > Web app (execute as Me, access Anyone).");
}

/** The web app: every answer so far, as JSON (or JSONP when ?callback=name is given). */
function doGet(e) {
  const responses = readResponses();
  const body = JSON.stringify({ grid: GRID_CENTS, maxBids: MAX_BIDS, now: new Date(), responses: responses });
  const callback = e && e.parameter && e.parameter.callback;
  if (callback && /^[A-Za-z_$][\w$]*$/.test(callback)) {
    return ContentService.createTextOutput(callback + "(" + body + ");")
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(body).setMimeType(ContentService.MimeType.JSON);
}

function readResponses() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets().find(s => s.getFormUrl());
  if (!sheet || sheet.getLastRow() < 2) return [];
  const rows = sheet.getDataRange().getValues();
  const header = rows[0].map(String);
  const col = title => header.indexOf(title);
  const nameCol = col("Your name or team");
  return rows.slice(1).map(row => {
    const bids = [];
    for (let i = 1; i <= MAX_BIDS; i++) {
      const a = row[col("Bid " + i + ": amount (kWh)")], p = row[col("Bid " + i + ": price (cents)")];
      const num = v => Number(String(v).replace(",", "."));     // "1,5" from comma-decimal locales
      if (a !== "" && p !== "" && isFinite(num(a)) && isFinite(num(p))) bids.push([num(a), num(p)]);
    }
    return { time: row[0], name: String(row[nameCol]).trim(), bids: bids };
  }).filter(r => r.name);
}
