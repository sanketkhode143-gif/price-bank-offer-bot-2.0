"""Small, dependency-free offer parser for rendered Amazon/Flipkart page text.

It deliberately returns "Not found" when the fetched page does not contain an
offer. It does not guess from unrelated EMI or cashback amounts.
"""

import re


MONEY = r"(?:₹|Rs\.?\s?|INR\s?)([0-9][0-9,]*(?:\.\d{1,2})?)"
EXCLUDED = re.compile(r"amazon\s*(?:pay\s*)?(?:icici)|bajaj\s*(?:finserv|finance)", re.I)
BANK_CARD = re.compile(
    r"\b((?:ICICI|HDFC|SBI|Axis|Kotak|IDFC|BOB|Bank of Baroda|HSBC|RBL|Federal|Yes Bank|Standard Chartered)"
    r"(?:\s+Bank)?\s+(?:Credit|Debit)\s+Card)\b",
    re.I,
)


def _amount(value):
  try:
    return int(round(float(value.replace(",", ""))))
  except (TypeError, ValueError):
    return 0


def _line_amount(line):
  match = re.search(MONEY, line, re.I)
  return _amount(match.group(1)) if match else 0


def _base_price(text):
  explicit = re.search(
      rf"(?:Current\s+(?:Product\s+)?Price|Selling\s+Price|Base\s+Price)\s*[:\-]?\s*{MONEY}",
      text,
      re.I,
  )
  if explicit:
    return _amount(explicit.group(1))

  candidates = []
  for line in text.splitlines():
    if not re.search(MONEY, line, re.I):
      continue
    if re.search(r"\b(?:MRP|list price|monthly|month|/mo|discount|cashback|save|off|emi|interest|coupon|bank offer)\b", line, re.I):
      continue
    amount = _line_amount(line)
    if amount >= 100:
      candidates.append(amount)
  return max(candidates) if candidates else 0


def _no_cost_regions(lines):
  regions = []
  for i, line in enumerate(lines):
    if re.search(r"no\s*cost\s*emi", line, re.I):
      regions.append((max(0, i - 2), min(len(lines), i + 9)))
  return regions


def parse_product_offers(title: str, page_text: str):
  """Return normalized offer fields from page text without an AI/API call."""
  text = re.sub(r"\r", "", page_text or "")
  lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
  lines = [line for line in lines if line]
  normalized = "\n".join(lines)

  tenure_months = []
  for start, end in _no_cost_regions(lines):
    region = " ".join(lines[start:end])
    tenure_months.extend(
        int(value)
        for value in re.findall(r"(?:x|×)\s*(\d{1,2})\s*m\b", region, re.I)
    )
    tenure_months.extend(
        int(value)
        for value in re.findall(r"\b(\d{1,2})\s*months?\b", region, re.I)
    )
  no_cost_tenure = f"{max(tenure_months)} Months" if tenure_months else "Not found"

  regions = _no_cost_regions(lines)
  emi_bank_discount = 0
  emi_bank_note = "Not found"
  emi_interest_discount = 0
  emi_interest_note = "Not found"
  for start, end in regions:
    region_lines = lines[start:end]
    for i, line in enumerate(region_lines):
      context = " ".join(region_lines[max(0, i - 1):min(len(region_lines), i + 2)])
      if EXCLUDED.search(context):
        continue
      card = BANK_CARD.search(context)
      # A discount is accepted only when the amount is explicitly negative or
      # labelled as a discount/saving, avoiding monthly EMI amounts.
      discount = re.search(
          rf"(?:[-−]\s*{MONEY}|(?:bank\s+)?discount\s*[:\-]?\s*{MONEY}|save\s*{MONEY})",
          context,
          re.I,
      )
      if card and discount and not emi_bank_discount:
        groups = [group for group in discount.groups() if group]
        emi_bank_discount = _amount(groups[-1]) if groups else 0
        emi_bank_note = card.group(1).strip()

      if re.search(r"interest\s+(?:discount|waiver|savings?)|interest\s+saved", line, re.I):
        amount = _line_amount(line)
        if not amount and i + 1 < len(region_lines):
          amount = _line_amount(region_lines[i + 1])
        if amount:
          emi_interest_discount = amount
          emi_interest_note = "No Cost EMI interest discount"

  coupon_discount = 0
  for line in lines:
    if re.search(r"coupon|voucher|apply\s+coupon", line, re.I) and not EXCLUDED.search(line):
      coupon_discount = max(coupon_discount, _line_amount(line))

  full_discount = 0
  full_note = "Not found"
  for i, line in enumerate(lines):
    if EXCLUDED.search(line) or re.search(r"no\s*cost\s*emi|monthly|interest", line, re.I):
      continue
    if not re.search(r"bank\s+discount|instant\s+discount|discount\s+on|save\s+₹|off\s+on", line, re.I):
      continue
    amount = _line_amount(line)
    if amount > full_discount:
      full_discount = amount
      card = BANK_CARD.search(line)
      full_note = card.group(1).strip() if card else "Bank offer"

  base = _base_price(normalized)
  return {
      "product_title": (title or "Product")[:80],
      "base": base,
      "coupon": coupon_discount,
      "full_discount": full_discount,
      "full_offer_text": full_note,
      "nc_emi_months": no_cost_tenure,
      "nc_emi_interest_discount": emi_interest_discount,
      "nc_emi_banks": emi_interest_note,
      "nc_emi_bank_discount": emi_bank_discount,
      "nc_emi_bank_note": emi_bank_note,
  }

