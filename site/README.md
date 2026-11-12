# Salted Honeycomb Collective site

One static page, `index.html`. No build step, no dependencies. Open it in a browser and it works.

## Launch checklist (three steps)

### 1. Stripe payment link for the $350 call

The Stripe connector in this Claude session is read-only, so create the link in Stripe directly (about 2 minutes):

1. In Stripe (Sweet Bee Academy account), go to **Payment Links > New**.
2. Create a product: **Salted Honeycomb Collective: 30-Minute 1:1 Call**, one-time, **$350.00**.
3. Copy the payment link URL (looks like `https://buy.stripe.com/...`).
4. In `index.html`, replace both occurrences of `STRIPE_PAYMENT_LINK_URL` with that URL.

Both the "Book the call" button and the Engagement's "Start with the call" button point at the same link on purpose. The $350 call is the qualification step for the $5K+ engagement. There is no self-checkout for the engagement.

### 2. Flodesk form

1. In Flodesk, create an **inline** form named **Salted Honeycomb Collective**, attached to a new segment with the same name.
2. Copy the form ID from the embed code (the long string in `fd-form-XXXX`).
3. In `index.html`, replace both occurrences of `FLODESK_FORM_ID` with it.

Subscribers then sync straight into Flodesk and are tagged by that segment.

### 3. Hosting and the Squarespace domain

GitHub Pages for this repo already serves the dashboard from `docs/`, so host this site elsewhere and point the domain at it:

1. Deploy the `site/` folder to any static host (Vercel and Netlify both deploy a folder in one step, free tier is fine).
2. Add `saltedhoneycombcollective.com` as a custom domain in that host.
3. In Squarespace: **Settings > Domains > saltedhoneycombcollective.com > DNS settings**. Add the records the host gives you (usually one A record for the root and a CNAME for `www`).

DNS changes take minutes to a few hours.

## Voice rules for editing copy

Follow the `my-writing-style` skill. No em dashes anywhere. No sentence starts with "Not". Short sentences, plain words, kind but straight.
