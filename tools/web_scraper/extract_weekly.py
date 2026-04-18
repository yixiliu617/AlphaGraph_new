"""Extract weekly price data from PCPartPicker chart images using Gemini vision."""

import os, json, re, base64, time, sys
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

GEMINI_KEY = os.environ['GEMINI_API_KEY']
DATA_DIR = 'backend/data/market_data/pcpartpicker_trends'
MANIFEST = f'{DATA_DIR}/image_manifest.json'

WEEKLY_PROMPT = '''You are analyzing a PCPartPicker price trend chart image.

The chart shows Average Price (USD) Over Last 18 Months for a PC component.
The thick black line is the average price. X-axis: dates. Y-axis: USD.

Extract the average price (black line) at WEEKLY intervals (every 7 days).
Start from the leftmost date visible and read a price every ~7 days.

Return ONLY a JSON array, no other text:
[
  {"date": "2024-10-07", "price": 50},
  {"date": "2024-10-14", "price": 50},
  ...
]

Read prices as precisely as you can. Round to nearest dollar.
Include ALL weekly points across the full 18-month chart.
'''

with open(MANIFEST) as f:
    manifest = json.load(f)

print(f'Extracting weekly data from {len(manifest)} charts...')
all_rows = []
errors = []

for i, entry in enumerate(manifest):
    title = entry['title']
    category = entry['category']
    local_path = entry['local_path']

    if not os.path.exists(local_path):
        print(f'  [{i+1}/{len(manifest)}] SKIP {title} (missing)')
        continue

    print(f'  [{i+1}/{len(manifest)}] {category}/{title}...', end=' ', flush=True)

    with open(local_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()

    url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}'
    payload = {
        'contents': [{'parts': [
            {'text': f'Component: {category} - {title}\n\n{WEEKLY_PROMPT}'},
            {'inline_data': {'mime_type': 'image/png', 'data': img_b64}},
        ]}],
        'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 16384},
    }

    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        text = resp.json()['candidates'][0]['content']['parts'][0]['text']
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)

        start = text.find('[')
        end = text.rfind(']')
        if start == -1 or end == -1:
            print('PARSE ERROR')
            errors.append(title)
            continue

        data = json.loads(text[start:end+1])
        print(f'{len(data)} weeks')

        for dp in data:
            all_rows.append({
                'category': category,
                'component': title,
                'date': dp['date'],
                'avg_price_usd': dp['price'],
            })

        if i < len(manifest) - 1:
            time.sleep(3)

    except Exception as e:
        print(f'ERROR: {e}')
        errors.append(title)

if all_rows:
    df = pd.DataFrame(all_rows)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(['category', 'component', 'date'])

    out = f'{DATA_DIR}/_combined_weekly.parquet'
    df.to_parquet(out, index=False, compression='zstd')
    print(f'\nSaved {len(df)} rows -> {out}')

    summary = df.groupby('category').agg(
        components=('component', 'nunique'),
        points=('avg_price_usd', 'count'),
    )
    print(summary.to_string())
else:
    print('No data extracted!')

if errors:
    print(f'\n{len(errors)} errors: {errors[:5]}')

print('\nDONE')
