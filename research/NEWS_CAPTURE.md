# Preparing a news-aware strategy

The PQT SDK already delivers the competition news in `on_news` and
`BatchState.news_stack`. An internet scraper would collect a different feed with
different timing. The first research step is to record the **SDK feed we could
actually trade on**, along with the public market state observed at each batch.

`strategies/news_capture.py` is a passive recorder. It saves every distinct news
ID, publication batch, first observed batch, headline, and body, plus each
batch's public top of book and trades. It records from the cumulative SDK stack,
so news present in a late-start or reconnect snapshot is captured even if the
SDK does not call `on_news` again. It places no orders and does not modify the
frozen v1 strategy or its competition freeze.

Run it in test mode with a **separate algorithm key** if the trading bot is also
connected. The guide allows only one connection per key; using the same key
would supersede the trading bot. The server URL must come from the organizers.
For a solo sandbox research session:

```bash
export PQT_SERVER='REPLACE_WITH_ORGANIZER_TEST_URL'
# Set PQT_API_KEY silently using the procedure in COMPETITION_CHECKLIST.md.
.venv/bin/pqt run strategies/news_capture.py --mode test --server "$PQT_SERVER"
```

The default output is a unique JSONL file under `results/news_capture/`, which
is gitignored. Set `PQT_NEWS_CAPTURE_DIR` to another local directory if desired.
No key is written. Each `news` row has `published_batch` and `first_seen_batch`;
`late_seen=true` means the recorder did not observe publication in real time.
Each `batch` row contains the public book and trades. A connection gap leaves
missing batch numbers; the recorder does not invent observations.

After collecting one or more files, create a descriptive event table:

```bash
.venv/bin/python -m research.analyze_news_capture results/news_capture/*.jsonl
```

The CSV has one row per news item and symbol. It shows whether the text names
the symbol, the immediate observed midprice change when a pre-news snapshot
exists, and 5/30/60-second public-mid moves measured from the **first batch in
which an order sent after seeing the item could execute**. Missing snapshots
stay blank. This is a research label, not the competition score or an executable
profit estimate. Historical s01–s24 files contain no news, so this pipeline
cannot yet be calibrated on those sessions.

Once enough real events are collected, evaluate whether simple, predeclared
features from the headline/body predict the *remaining* move after the first
executable batch, net of spread, fees, fill uncertainty, and the existing
microprice baseline. Split by session or event time before choosing any rule.
Start with a shadow prediction that cannot place trades; only a separate tested
release should replace the current news pause. If a pretrained text model is
used, account for possible look-ahead contamination of historical news tests
([Glasserman and Lin, 2023](https://arxiv.org/abs/2309.17322)).
