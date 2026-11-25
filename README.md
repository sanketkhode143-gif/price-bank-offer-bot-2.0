# Price Bank Offer Bot 2.0

This is a separate local copy of the original bot. The original GitHub repository and its `main` branch are unchanged.

## Changes in 2.0

- Removed Gemini API calls and the `GEMINI_API_KEY` requirement.
- Added a deterministic parser for the product page text already fetched by the bot.
- The parser recognizes visible price, coupon, No Cost EMI tenure, interest discount, and regular bank card discount patterns.
- When offer details are absent from the fetched page, the Telegram reply reports that they were not found instead of showing a misleading ₹0 discount or net price.

No paid AI API is used. The parser can only read details returned in Amazon or Flipkart's fetched page HTML; offers that appear only after browser interaction may not be available.
