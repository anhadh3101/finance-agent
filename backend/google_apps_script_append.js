/**
 * Optional webhook for /adddemo when you do not have a service account handy.
 *
 * 1. Open the Google Sheet -> Extensions -> Apps Script
 * 2. Paste this file, set APPEND_SECRET below, deploy as Web app
 *    (Execute as: Me, Who has access: Anyone)
 * 3. Put the deployment URL in .env as GOOGLE_SHEET_APPEND_WEBHOOK
 * 4. Put the same secret in GOOGLE_SHEET_APPEND_SECRET
 *
 * POST body:
 * {
 *   "secret": "your-secret",
 *   "rows": [["2026-09-08","DoorDash","32.50","food_delivery"], ...]
 * }
 */
const APPEND_SECRET = "change-me";

function doPost(e) {
  const payload = JSON.parse(e.postData.contents || "{}");
  if (payload.secret !== APPEND_SECRET) {
    return json({ ok: false, error: "unauthorized" });
  }

  const rows = payload.rows || [];
  if (!rows.length) {
    return json({ ok: false, error: "no rows" });
  }

  const sheet =
    SpreadsheetApp.getActiveSpreadsheet().getSheetByName("transactions");
  if (!sheet) {
    return json({ ok: false, error: "transactions tab not found" });
  }

  rows.forEach(function (row) {
    sheet.appendRow(row);
  });

  return json({ ok: true, appended: rows.length });
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(
    ContentService.MimeType.JSON
  );
}
