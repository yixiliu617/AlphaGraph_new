import pandas as pd
import os

parquet_path = r"backend/data/filing_data/filings/raw/year=2025.parquet"

if os.path.exists(parquet_path):
    df = pd.read_parquet(parquet_path)
    print("Columns:", df.columns.tolist())
    print("\nFirst 2 rows:")
    print(df.head(2))
else:
    print(f"File not found: {parquet_path}")
