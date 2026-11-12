/**
 * Live classroom market: collects bids with a Google Form and serves them to big_screen.html.
 *
 * Paste this whole file into Extensions > Apps Script of an EMPTY Google Sheet, then:
 *   1. run setup() once -- it creates the form, deals the role cards into it and links its answers to this sheet;
 *   2. Deploy > New deployment > Web app, execute as "Me", access "Anyone";
 *   3. give students the form link, or each student their own card link (both in the "Card links" sheet),
 *      and paste the web app URL into big_screen.html.
 * A new deck later: change DECK_CODE or CARDS below, save, and run updateCards(); the form keeps its link.
 * Full instructions: docs/live_market/README.md.
 */

const GRID_CENTS = 30;   // the grid price: the highest price anyone may bid, in cents
const MAX_BIDS = 5;      // bids per student
const CARDS = 10;        // role cards, one per student (see role_cards.html); 0 = no cards, students just bid
const DECK_CODE = "1";   // a new code deals new numbers on the same households

const GRID = GRID_CENTS;
const NAME_TITLE = "Your name or team";
const CARD_TITLE = "Your card number";
const BIDS_TITLE = "Your bids";

function setup() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet();
  const form = FormApp.create("P2P market: submit your bid");
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());
  form.setCollectEmail(false);
  form.setShowLinkToRespondAgain(true);
  form.setConfirmationMessage("Bid received. Watch the big screen!");
  try {
    form.setRequireLogin(false);   // school/company Google accounts restrict forms to the domain by default
  } catch (e) {
    // personal Google accounts have no such setting
  }

  form.addTextItem().setTitle(NAME_TITLE).setRequired(true).setHelpText("Shown on the big screen.");
  if (CARDS > 0) form.addListItem().setTitle(CARD_TITLE).setRequired(true)
    .setHelpText("The number you were given. The next page shows your household.");
  form.addPageBreakItem().setTitle(BIDS_TITLE).setHelpText(
    "Up to " + MAX_BIDS + " bids for one hour of the peer-to-peer market.\n\n" +
    "Amount in kWh: positive to BUY, negative to SELL (4 = buy up to 4 kWh, -3 = sell up to 3 kWh).\n" +
    "Price in cents, 0 to " + GRID_CENTS + ": a buy counts at that price and every lower price, " +
    "a sell at that price and every higher price.\n\n" +
    "Leave unused bids empty. You can submit again: only your latest answer counts.");
  for (let i = 1; i <= MAX_BIDS; i++) {
    form.addTextItem().setTitle("Bid " + i + ": amount (kWh)")
      .setHelpText(i === 1 ? "+ to buy, - to sell, e.g. 4 or -3" : "")
      .setValidation(FormApp.createTextValidation().requireNumber()
        .setHelpText("A number: positive to buy, negative to sell.").build());
    form.addTextItem().setTitle("Bid " + i + ": price (cents)")
      .setValidation(FormApp.createTextValidation().requireNumberBetween(0, GRID_CENTS)
        .setHelpText("A price from 0 to " + GRID_CENTS + " cents.").build());
  }
  PropertiesService.getScriptProperties().setProperty("formId", form.getId());
  updateCards();

  Logger.log("Form for students:  " + form.getPublishedUrl());
  Logger.log("Edit the form:      " + form.getEditUrl());
  Logger.log("Next: Deploy > New deployment > Web app (execute as Me, access Anyone).");
}

/** Deals CARDS cards with DECK_CODE into the form: one page per card, reached through the card number. */
function updateCards() {
  const form = currentForm();
  const items = form.getItems();
  const list = items.filter(it => it.getTitle() === CARD_TITLE && it.getType() === FormApp.ItemType.LIST)[0];
  const props = PropertiesService.getScriptProperties();
  props.setProperties({ cards: String(list ? CARDS : 0), deck: DECK_CODE });
  if (!list) { Logger.log("This form has no card question: students just bid."); return; }

  const bids = items.filter(it => it.getTitle() === BIDS_TITLE && it.getType() === FormApp.ItemType.PAGE_BREAK)[0].asPageBreakItem();
  const pages = items.filter(it => it.getType() === FormApp.ItemType.PAGE_BREAK && /^Card \d+/.test(it.getTitle()))
    .map(it => it.asPageBreakItem());
  const cardList = list.asListItem();
  const cards = deal(CARDS, DECK_CODE);
  cardList.setChoiceValues(["1"]);                              // no choice points at a page while pages change
  while (pages.length > cards.length) form.deleteItem(pages.pop().getIndex());
  while (pages.length < cards.length) {
    const page = form.addPageBreakItem();                       // added at the end: move it before the bids page
    form.moveItem(page.getIndex(), bids.getIndex());
    pages.push(page);
  }
  cards.forEach((card, i) => {
    pages[i].setTitle("Card " + card.no + ": " + card.role.title).setHelpText(cardText(card));
    // A page break's "go to" applies when the page before it ends: after card i, go straight to the bids.
    if (i > 0) pages[i].setGoToPage(bids);
  });
  cardList.setChoices(cards.map((card, i) => cardList.createChoice(String(card.no), pages[i])));

  // One pre-filled link per card, in a sheet of its own, to send each student their card.
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("Card links") || ss.insertSheet("Card links");
  sheet.clear();
  const rows = [["card", "household", "link (opens the form with this card chosen)"]].concat(cards.map(card =>
    [card.no, card.role.title, form.createResponse().withItemResponse(cardList.createResponse(String(card.no))).toPrefilledUrl()]));
  sheet.getRange(1, 1, rows.length, 3).setValues(rows);
  sheet.getRange(rows.length + 2, 1).setValue("Form for everyone: " + form.getPublishedUrl() + "   (deck " + DECK_CODE + ")");
  Logger.log("Dealt " + cards.length + " cards, deck " + DECK_CODE + ". Links for each card: the 'Card links' sheet.");
}

function cardText(card) {
  const buy = card.side === "buy";
  const tiers = card.tiers.slice().sort((a, b) => buy ? b[1] - a[1] : a[1] - b[1]);
  let from = 0;
  const lines = tiers.map(([kwh, cents]) => {
    const range = from === 0 ? "the first " + kwh + " kWh" : "the next " + kwh + " kWh";
    from += kwh;
    return "• " + range + ": " + cents + "¢" + (buy && cents === GRID ? " (else from the grid)" : "");
  });
  lines.push(buy ? "• more than " + from + " kWh: 0¢ (you cannot use it)"
                 : "• more than " + from + " kWh: you do not have it, so you buy it from the grid at " + GRID + "¢");
  return (buy ? "YOU BUY. " : "YOU SELL. ") + card.role.story(card.p).replace(/\s+/g, " ") + "\n\n" +
    (buy ? "What the energy is worth to you at most:\n" : "The least you accept for your energy:\n") + lines.join("\n") + "\n\n" +
    "Your score: for every kWh you " + (buy ? "buy, what it is worth to you minus the market price."
                                            : "sell, the market price minus the least you accept.") +
    " You do not have to bid your true values. Bid " + (buy ? "positive" : "negative") + " amounts.\n\n" +
    "Next: your bids. Back brings you to this card again.";
}

function currentForm() {
  const id = PropertiesService.getScriptProperties().getProperty("formId");
  if (id) return FormApp.openById(id);
  const url = SpreadsheetApp.getActiveSpreadsheet().getFormUrl();
  if (!url) throw new Error("No form yet: run setup() first.");
  return FormApp.openByUrl(url);
}

/** The web app: every answer so far and the cards, as JSON (or JSONP when ?callback=name is given). */
function doGet(e) {
  const props = PropertiesService.getScriptProperties();
  const count = Number(props.getProperty("cards") || 0), code = props.getProperty("deck") || DECK_CODE;
  const cards = deal(count, code).map(c => ({ no: c.no, title: c.role.title, side: c.side, tiers: c.tiers }));
  const body = JSON.stringify({ grid: GRID_CENTS, maxBids: MAX_BIDS, now: new Date(), deck: code, cards: cards,
                                responses: readResponses(props.getProperty("formId")) });
  const callback = e && e.parameter && e.parameter.callback;
  if (callback && /^[A-Za-z_$][\w$]*$/.test(callback)) {
    return ContentService.createTextOutput(callback + "(" + body + ");")
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(body).setMimeType(ContentService.MimeType.JSON);
}

function readResponses(formId) {
  // The answers of this form (an older form linked to the same sheet keeps its own answer sheet).
  const linked = SpreadsheetApp.getActiveSpreadsheet().getSheets().filter(s => s.getFormUrl());
  const sheet = linked.filter(s => formId && s.getFormUrl().indexOf(formId) >= 0)[0] || linked[0];
  if (!sheet || sheet.getLastRow() < 2) return [];
  const rows = sheet.getDataRange().getValues();
  const header = rows[0].map(String);
  const col = title => header.indexOf(title);
  const nameCol = col(NAME_TITLE), cardCol = col(CARD_TITLE);
  const num = v => Number(String(v).replace(",", "."));          // "1,5" from comma-decimal locales
  return rows.slice(1).map(row => {
    const bids = [];
    for (let i = 1; i <= MAX_BIDS; i++) {
      const a = row[col("Bid " + i + ": amount (kWh)")], p = row[col("Bid " + i + ": price (cents)")];
      if (a !== "" && p !== "" && isFinite(num(a)) && isFinite(num(p))) bids.push([num(a), num(p)]);
    }
    const card = cardCol >= 0 && row[cardCol] !== "" ? num(row[cardCol]) : null;
    return { time: row[0], name: String(row[nameCol]).trim(), card: card, bids: bids };
  }).filter(r => r.name);
}

// BEGIN DECK -- identical in role_cards.html and apps_script.gs, so the form and the printed cards match; keep them in step.
// The eight households, dealt in this order so that any number of students gets a mix of buyers and sellers.
// Each parameter is drawn from [low, high]; tiers are [kWh, cents]: for a buyer what the kWh are worth at most,
// for a seller the least it would accept for them.
const ROLES = [
  { title: "Empty battery", side: "buy", params: { must: [2, 4], later: [8, 12] },
    tiers: p => [[p.must, GRID], [2, p.later]],
    story: p => `Your home battery is almost empty. Over the next two hours your home uses ${p.must} kWh: whatever you
      do not buy here, you buy from the grid at ${GRID}¢. In two hours the price drops to ${p.later}¢, so 2 more kWh
      to fill the battery now are worth at most ${p.later}¢ to you.` },
  { title: "Solar roof, no battery", side: "sell", params: { kwh: [4, 6], feedin: [4, 7] },
    tiers: p => [[p.kwh, p.feedin]],
    story: p => `The sun is out: your solar panels make ${p.kwh} kWh more than your home uses, and you cannot store it.
      Whatever you do not sell here goes to the grid at the feed-in tariff of ${p.feedin}¢.` },
  { title: "Heat pump, cold house", side: "buy", params: { must: [2, 3], comfort: [14, 18] },
    tiers: p => [[p.must, GRID], [2, p.comfort]],
    story: p => `It is cold. Your heat pump needs ${p.must} kWh to keep the house at its setpoint; whatever you do not
      buy here comes from the grid at ${GRID}¢. Another 2 kWh would pre-heat the house for the evening, which is
      worth ${p.comfort}¢/kWh to you in comfort.` },
  { title: "Full battery", side: "sell", params: { spare: [2, 3], tomorrow: [12, 16], keep: [2, 3], evening: [22, 26] },
    tiers: p => [[p.spare, p.tomorrow], [p.keep, p.evening]],
    story: p => `Your battery is full. ${p.spare} kWh are spare: you could also sell them tomorrow for about
      ${p.tomorrow}¢. Another ${p.keep} kWh are meant for this evening, when they save you ${p.evening}¢/kWh; you
      would only sell those for more.` },
  { title: "Electric car, leaving soon", side: "buy", params: { kwh: [3, 5], fast: [21, 25] },
    tiers: p => [[p.kwh, p.fast]],
    story: p => `You leave in two hours and your car needs ${p.kwh} kWh more. Whatever you do not get here, you charge
      on the way at a fast charger for ${p.fast}¢/kWh.` },
  { title: "Solar roof with battery", side: "sell", params: { now: [2, 4], low: [7, 9], later: [2, 3], high: [17, 19] },
    tiers: p => [[p.now, p.low], [p.later, p.high]],
    story: p => `Your panels make more than you use and your battery is nearly full: ${p.now} kWh go to the grid at
      ${p.low}¢ unless you sell them here. Another ${p.later} kWh could stay in the battery for later, where they are
      worth ${p.high}¢/kWh to you.` },
  { title: "Workshop with a deadline", side: "buy", params: { must: [2, 4], extra: [1, 2], value: [17, 21] },
    tiers: p => [[p.must, GRID], [p.extra, p.value]],
    story: p => `Your machines must run for an order: ${p.must} kWh, from the grid at ${GRID}¢ if not from here.
      ${p.extra} kWh more would let you start the next job early; that is worth ${p.value}¢/kWh to you.` },
  { title: "Solar home that can turn down", side: "sell", params: { surplus: [2, 3], feedin: [5, 7], comfort: [19, 23] },
    tiers: p => [[p.surplus, p.feedin], [2, p.comfort]],
    story: p => `Your solar panels make ${p.surplus} kWh more than your home uses (feed-in tariff: ${p.feedin}¢). You
      could also switch off your heat pump for an hour and sell the 2 kWh it would use, but the house gets cooler: you
      want at least ${p.comfort}¢/kWh for that.` },
];

// ---------------------------------------------------------------- deck
function rng(seedText) {                       // string hash + mulberry32: the same code always deals the same deck
  let h = 1779033703 ^ seedText.length;
  for (let i = 0; i < seedText.length; i++) { h = Math.imul(h ^ seedText.charCodeAt(i), 3432918353); h = (h << 13) | (h >>> 19); }
  let a = h >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function deal(count, seedText) {
  const rand = rng(seedText);
  const cards = [];
  for (let i = 0; i < count; i++) {
    const role = ROLES[i % ROLES.length];
    const p = {};
    for (const [k, [lo, hi]] of Object.entries(role.params)) p[k] = lo + Math.floor(rand() * (hi - lo + 1));
    cards.push({ no: i + 1, role, p, side: role.side, tiers: role.tiers(p) });
  }
  return cards;
}
// END DECK
