import json
import os
import re
import threading
import time
from flask import Flask
import requests

# Tumcha tracker.py madhla logic reuse karnyasathi
from offer_parser import parse_product_offers
from tracker import clean_url, get_page_info, send_tg

app = Flask(__name__)

TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_CHATS = [
    c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()
]
DEFAULT_CHAT_ID = ALLOWED_CHATS[0]
TG_BASE = "https://api." + "telegram.org/bot" + TG_TOKEN


def load_json(path, default):
  if os.path.exists(path):
    try:
      return json.load(open(path))
    except Exception:
      return default
  return default


def save_json(path, data):
  with open(path, "w") as f:
    json.dump(data, f, indent=2)


def fetch_and_reply_product(item, products, history):
  pname = item.get("name", "Product")
  url = item["url"]
  chat_id = item.get("chat_id", DEFAULT_CHAT_ID)
  reply_to = item.get("reply_to")

  scraped_title, text = get_page_info(url)
  if len(text) < 800:
    send_tg(
        f"⚠️ *Could not read full page details right now* for *{pname}*.\n"
        "Tracker will retry automatically.",
        chat_id=chat_id,
        reply_to=reply_to,
    )
    return

  data = parse_product_offers(pname, text)
  print("Parsed offers:", data, flush=True)
  if not data or not data.get("base"):
    send_tg(
        "⚠️ I could not find the current price in the page data. "
        "The store may be blocking the request or loading details in the browser.",
        chat_id=chat_id,
        reply_to=reply_to,
    )
    return

  if pname in ("Product", "", None) or len(pname) < 4:
    ai_title = (data.get("product_title") or "").strip()
    if ai_title and ai_title.lower() != "product":
      pname = ai_title[:60]
    elif scraped_title and scraped_title.lower() != "product":
      pname = scraped_title[:60]
    item["name"] = pname
    save_json("products.json", products)

  base = int(round(float(data.get("base") or 0)))
  coupon = int(round(float(data.get("coupon") or 0)))
  full_disc = int(round(float(data.get("full_discount") or 0)))
  net = max(0, base - coupon - full_disc)

  emi_disc = int(round(float(data.get("nc_emi_interest_discount") or 0)))
  emi_bank_disc = int(round(float(data.get("nc_emi_bank_discount") or 0)))
  emi_net = max(0, base - coupon - emi_disc - emi_bank_disc)

  data["base"] = base
  data["coupon"] = coupon
  data["full_discount"] = full_disc
  data["net"] = net
  data["nc_emi_interest_discount"] = emi_disc
  data["nc_emi_bank_discount"] = emi_bank_disc
  data["emi_net"] = emi_net

  history[url] = data
  save_json("price_history.json", history)

  coupon_line = f"• *Coupon Discount:* ₹{coupon:,}\n" if coupon > 0 else ""
  full_offer_txt = data.get("full_offer_text") or "Not found on page"
  nc_months = data.get("nc_emi_months") or "Not found on page"
  nc_banks = data.get("nc_emi_banks") or "Not found on page"
  nc_bank_note = data.get("nc_emi_bank_note") or "Not found on page"
  full_discount_text = (
      f"₹{full_disc:,} ({full_offer_txt})" if full_disc > 0 else full_offer_txt
  )
  interest_discount_text = (
      f"₹{emi_disc:,} ({nc_banks})" if emi_disc > 0 else nc_banks
  )
  emi_bank_discount_text = (
      f"₹{emi_bank_disc:,} ({nc_bank_note})"
      if emi_bank_disc > 0
      else nc_bank_note
  )
  full_net_text = (
      f"₹{net:,}" if full_disc > 0 or coupon > 0 else "Not calculated; offer not found"
  )
  emi_net_text = (
      f"₹{emi_net:,}"
      if emi_disc > 0 or emi_bank_disc > 0 or coupon > 0
      else "Not calculated; offer not found"
  )

  msg = (
      f"🆕 *INITIAL PRICE & OFFERS*\n\n"
      f"*{pname}*\n"
      f"• *Base Price:* ₹{base:,}\n"
      f"{coupon_line}\n"
      f"💳 *Full Payment Offer:*\n"
      f"• *Bank Discount:* {full_discount_text}\n"
      f"• *Final Net Price:* *{full_net_text}*\n\n"
      f"📅 *No Cost EMI (Excl. Amazon ICICI & Bajaj):*\n"
      f"• *Max No Cost Tenure:* {nc_months}\n"
      f"• *Interest Discount:* {interest_discount_text}\n"
      f"• *Bank Discount:* {emi_bank_discount_text}\n"
      f"• *Net Cost (Base - Coupon - Interest - Bank):* *{emi_net_text}*\n\n"
      f"[Open Product Link]({url})"
  )
  send_tg(msg, chat_id=chat_id, reply_to=reply_to)


def poll_loop():
  offset = 0
  while True:
    try:
      r = requests.get(
          f"{TG_BASE}/getUpdates",
          params={"offset": offset, "timeout": 25},
          timeout=35,
      ).json()

      products = load_json("products.json", [])
      history = load_json("price_history.json", {})

      for upd in r.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        msg_id = msg.get("message_id")

        if chat_id not in ALLOWED_CHATS:
          continue

        text = (msg.get("text") or "").strip()
        if not text:
          continue

        if text.lower() == "/list":
          if not products:
            send_tg(
                "📋 *Your tracking list is empty.* Send any product link to add"
                " it!",
                chat_id=chat_id,
                reply_to=msg_id,
            )
          else:
            lines = [
                f"{i+1}. *{p['name']}*\n   {p['url']}"
                for i, p in enumerate(products)
            ]
            send_tg(
                "📋 *Currently Tracking Products:*\n\n" + "\n".join(lines),
                chat_id=chat_id,
                reply_to=msg_id,
            )

        elif text.lower().startswith("/remove"):
          parts = text.split()
          if len(parts) == 2 and parts[-1].isdigit():
            idx = int(parts[-1]) - 1
            if 0 <= idx < len(products):
              removed = products.pop(idx)
              history.pop(removed["url"], None)
              save_json("products.json", products)
              save_json("price_history.json", history)
              send_tg(
                  f"🗑️ *Removed:* {removed['name']}",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )
            else:
              send_tg(
                  "⚠️ Invalid number. Send `/list` to see product numbers.",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )

        else:
          urls = re.findall(
              r"(?:https?://)?(?:www\.)?(?:amazon\.in|amzn\.in|amzn\.to|flipkart\.com|dl\.flipkart\.com)\S+",
              text,
          )
          if urls:
            raw_url = next(iter(urls))
            if not raw_url.startswith("http"):
              raw_url = "https://" + raw_url
            cleaned = clean_url(raw_url)
            custom_name = text.replace(next(iter(urls)), "").strip()

            existing = next((p for p in products if p["url"] == cleaned), None)
            if existing:
              send_tg(
                  f"ℹ️ *Already in your list:*\n{cleaned}\n⏳ Fetching latest"
                  " offers...",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )
              fetch_and_reply_product(existing, products, history)
            else:
              auto_title, _ = get_page_info(cleaned)
              final_name = custom_name if custom_name else auto_title
              new_item = {
                  "name": final_name,
                  "url": cleaned,
                  "chat_id": chat_id,
                  "reply_to": msg_id,
              }
              products.append(new_item)
              save_json("products.json", products)

              send_tg(
                  f"✅ *Added & Checking Live Offers...*\n\n"
                  f"• *Name:* {final_name}\n"
                  f"• *URL:* {cleaned}",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )
              fetch_and_reply_product(new_item, products, history)

    except Exception as e:
      print("Listener error:", e, flush=True)
      time.sleep(3)


@app.route("/")
def health():
  return "Bot Listener Active", 200


if __name__ == "__main__":
  t = threading.Thread(target=poll_loop, daemon=True)
  t.start()
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)

    
