import pandas as pd
from utils.logger import logger

mandatory_columns = {
    "material_data": ["mat_id", "material_name"],
    "annual_data": ["unit"]
}

default_values = {
    "invoice_data": {"Currency": "USD"},
    "annual_data": {"Currency": "USD"},
    "cost_data": {"Currency": "USD"},
    "surcharge_data": {"Currency": "USD"}
}

def run_validation(df: pd.DataFrame, table_name: str, date_cols: list = []) -> tuple:
    if df is None or df.empty:
        logger.error(f"{table_name}: dateframe is empty before validation")
        return pd.DataFrame(), 0
    
    original_count = len(df)

    defaults = default_values.get(table_name, {})
    for col, default_val in defaults.items():
        if col in df.columns:
            filled = df[col] = df[col].isnull().sum()
            if filled > 0:
                df[col] = df[col].fillna(default_val)
                logger.info(f"{table_name}: filled {filled} null values in '{col}' with '{default_val}'")

    before = len(df)
    df = df.drop_duplicates()
    removed = before - len(df)
    if removed > 0:
        logger.warning(f"{table_name}: {removed} duplicates rows removed")

    if date_cols:
        for col in df.columns:
            if col in df.columns:
                invalid_mask = df[col].isnull()
                invalid_count = invalid_mask.sum()
                if invalid_count > 0:
                    logger.warning(f"{table_name}: {invalid_count} rows with invalid date in '{col} removed")
                df = df[~invalid_mask]

    if mandatory_columns:
        existing_mandatory = [c for c in mandatory_columns if c in df.columns]
        before = len(df)
        df = df.dropna(subset=existing_mandatory)
        removed = before - len(df)
        if removed > 0:
            logger.warning(f"{table_name}: {removed} rows rejected - missing mandatory columns {existing_mandatory}")

    rejected_counts = original_count - len(df)

    if rejected_counts > 0:
        logger.warning(f"{table_name}: {rejected_counts} total rows rejected out of {original_count}")
    else:
        logger.info(f"{table_name}: all {original_count} rows passed validation")

    return df, rejected_counts