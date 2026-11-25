import os
import re
import json
from html import unescape
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
import requests
from offer_parser import parse_product_offers as _parse_page_offers

TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEFAULT_CHAT_ID = [
    c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()
][0]

TG_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"


def clean_url(url: str) -> str:
  url = url.strip()
  if not url.startswith("http"):
    url = "https://" + url
  parsed = urlparse(url)

  # Flipkart URLs: keep pid/lid so product & offer pages resolve properly
  if "flipkart.com" in parsed.netloc:
    qs = parse_qs(parsed.query)
    kept = {}
    for k in ("pid", "lid"):
      if k in qs and qs[k]:
        kept[k] = qs[k][0]
    new_query = urlencode(kept)
    return urlunparse((
        parsed.scheme or "https",
        parsed.netloc,
        parsed.path,
        "",
        new_query,
        "",
    ))

  # Amazon URLs: keep clean /dp/ASIN path
  m = re.search(r"/(?:dp|gp/product|aw/d)/([A-Z0-9]{10})", parsed.path)
  if m:
    host = parsed.netloc or "www.amazon.in"
    return f"https://{host}/dp/{m.group(1)}"

  return urlunparse(
      (parsed.scheme or "https", parsed.netloc, parsed.path, "", "", "")
  )


def get_page_info(url: str):
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
      ),
      "Accept": (
          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
      ),
      "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
      "Referer": "https://www.google.co.in/",
  }

  html = ""
  for impersonate in ("chrome124", "chrome120", "safari17_0"):
    try:
      resp = cffi_requests.get(
          url,
          headers=headers,
          impersonate=impersonate,
          timeout=25,
          allow_redirects=True,
      )
      if resp.status_code == 200 and len(resp.text) > 1500:
        html = resp.text
        break
    except Exception as e:
      print(f"Fetch error ({impersonate}): {e}", flush=True)

  if not html:
    try:
      resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
      html = resp.text
    except Exception as e:
      print(f"Fallback requests error: {e}", flush=True)
      return "Product", ""

  soup = BeautifulSoup(html, "html.parser")
  page_title = ""
  heading = soup.select_one("#productTitle, h1 span, h1")
  if heading:
    page_title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True)).strip()

  # Prefer structured product prices, so MRP/EMI/discount amounts cannot be
  # mistaken for the actual selling price by the text parser.
  current_price = ""
  price_selectors = (
      "#corePrice_feature_div .a-offscreen",
      "#priceblock_ourprice",
      "#priceblock_dealprice",
      "meta[itemprop='price']",
      "meta[property='product:price:amount']",
      "._30jeq3",
      "[itemprop='price']",
  )
  for selector in price_selectors:
    node = soup.select_one(selector)
    if node:
      current_price = node.get("content") or node.get_text(" ", strip=True)
      if current_price:
        break

  # Some Amazon pages keep the offer cards in JSON-LD or keyword-bearing
  # script payloads. Preserve only those small relevant snippets.
  extra_text = []
  for script in soup.select("script[type='application/ld+json']"):
    try:
      structured = json.loads(script.string or script.get_text())
      objects = structured if isinstance(structured, list) else [structured]
      for obj in objects:
        if isinstance(obj, dict) and obj.get("@type") == "Product":
          offers = obj.get("offers", {})
          if isinstance(offers, list):
            offers = offers[0] if offers else {}
          if isinstance(offers, dict) and offers.get("price"):
            current_price = current_price or str(offers["price"])
    except (ValueError, TypeError):
      pass
  for script in soup.find_all("script"):
    raw = script.string or script.get_text()
    if raw and re.search(r"no\s*cost\s*emi|interest\s+discount|credit\s+card", raw, re.I):
      for match in re.finditer(r"no\s*cost\s*emi|interest\s+discount|credit\s+card", raw, re.I):
        snippet = unescape(raw[max(0, match.start() - 250):match.end() + 400])
        extra_text.append(BeautifulSoup(snippet, "html.parser").get_text(" ", strip=True))

  for tag in soup(["script", "style", "noscript", "svg"]):
    tag.decompose()

  title = page_title
  if not title and soup.title and soup.title.string:
    title = re.sub(r"\s+", " ", soup.title.string).strip()
  for suffix in (" : Amazon.in", " - Buy ", " | Flipkart.com"):
    if suffix in title:
      title = title.split(suffix)[0].strip()

  lines = [
      re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()
  ]
  text = "\n".join(ln for ln in lines if ln)
  if current_price:
    price_digits = re.sub(r"[^0-9,.]", "", str(current_price))
    if price_digits:
      text = f"Current Product Price: ₹{price_digits}\n" + text
  if extra_text:
    text += "\n" + "\n".join(extra_text)
  return (title[:80] if title else "Product"), text


def parse_product_offers(title: str, page_text: str):
  data = _parse_page_offers(title, page_text)
  return {
      "product_name": data.get("product_title") or title or "Product",
      "base_price": data.get("base") or 0,
      "coupon_discount": data.get("coupon") or 0,
      "full_payment_bank_discount": data.get("full_discount") or 0,
      "full_payment_bank_note": data.get("full_offer_text") or "Not found",
      "no_cost_tenure": data.get("nc_emi_months") or "Not found",
      "no_cost_interest_discount": data.get("nc_emi_interest_discount") or 0,
      "no_cost_interest_note": data.get("nc_emi_banks") or "Not found",
      "no_cost_bank_discount": data.get("nc_emi_bank_discount") or 0,
      "no_cost_bank_note": data.get("nc_emi_bank_note") or "Not found",
  }


def _fmt_inr(val):
  try:
    n = int(round(float(val)))
    return f"₹{n:,}"
  except Exception:
    return str(val)


def format_offer_message(
    data: dict, url: str, header_tag: str = "🆕 INITIAL PRICE & OFFERS"
):
  name = data.get("product_name") or "Product"
  base = int(round(float(data.get("base_price") or 0)))
  coupon = int(round(float(data.get("coupon_discount") or 0)))

  fp_bank = int(round(float(data.get("full_payment_bank_discount") or 0)))
  fp_note = (data.get("full_payment_bank_note") or "").strip()
  fp_net = max(0, base - coupon - fp_bank)

  tenure = (data.get("no_cost_tenure") or "None").strip()
  nc_int = int(round(float(data.get("no_cost_interest_discount") or 0)))
  nc_int_note = (data.get("no_cost_interest_note") or "").strip()
  nc_bank = int(round(float(data.get("no_cost_bank_discount") or 0)))
  nc_bank_note = (data.get("no_cost_bank_note") or "").strip()

  nc_net = max(0, base - coupon - nc_int - nc_bank)

  fp_bank_str = (
      f"{_fmt_inr(fp_bank)} ({fp_note})" if fp_note else _fmt_inr(fp_bank)
  )
  nc_int_str = (
      f"{_fmt_inr(nc_int)} ({nc_int_note})" if nc_int_note else _fmt_inr(nc_int)
  )
  nc_bank_str = (
      f"{_fmt_inr(nc_bank)} ({nc_bank_note})"
      if nc_bank_note
      else _fmt_inr(nc_bank)
  )

  return (
      f"*{header_tag}*\n\n"
      f"*{name}*\n"
      f"• Base Price: {_fmt_inr(base)}\n\n"
      f"💳 *Full Payment Offer:*\n"
      f"• Bank Discount: {fp_bank_str}\n"
      f"• Final Net Price: {_fmt_inr(fp_net)}\n\n"
      f"📅 *No Cost EMI (Excl. Amazon ICICI & Bajaj):*\n"
      f"• Max No Cost Tenure: {tenure}\n"
      f"• Interest Discount: {nc_int_str}\n"
      f"• Bank Discount: {nc_bank_str}\n"
      f"• Net Cost (Base - Coupon - Interest - Bank): {_fmt_inr(nc_net)}\n\n"
      f"[Open Product Link]({url})"
  )


def send_tg(msg: str, chat_id: str = None, reply_to: int = None):
  target_chat = chat_id or DEFAULT_CHAT_ID
  payload = {
      "chat_id": target_chat,
      "text": msg,
      "parse_mode": "Markdown",
      "disable_web_page_preview": True,
  }
  if reply_to:
    payload["reply_to_message_id"] = reply_to
  try:
    r = requests.post(f"{TG_BASE}/sendMessage", json=payload, timeout=15)
    if not r.ok and "parse_mode" in payload:
      payload.pop("parse_mode")
      requests.post(f"{TG_BASE}/sendMessage", json=payload, timeout=15)
  except Exception as e:
    print("Telegram send error:", e, flush=True)
