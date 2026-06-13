import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import ollama
from sqlalchemy import text
import pandas as pd
from utils.db import get_engine
from utils.logger import logger

#DATABASE SCHEMA CONTEXT
#gives LLM the structure to write correct answer(SQL)

DB_SCHEMA = """
You are a SQL expert for a Supplier Intelligence Platform.
You only generate SELECT queries. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE or any data modifying SQL.

Database: procurement_system
Tables and columns:

1. material_data
   - Mat. ID, Material Name, Classification, Alt. Material, Wt. (lbs), Heat Treatment, Coating

2. invoice_data
   - Invoice No., Supp. ID, Mat. ID, Invoice Dt., Qty, Unit Price $, Total Amt, Currency

3. annual_data
   - Mat. ID, Supp. ID, Commodity, Destination, Year, Ann. Volume, Unit, Total Cost $, Currency

4. cost_data
   - Mat. ID, Supp. ID, Commodity, Destination, Year, Mat. Category, Base Cost, Surcharge Cost, Variance, Currency

5. surcharge_data
   - Supp. ID, Mat. ID, Currency, Eff. Date

6. price_snapshots
   - mat_id, supplier_id, snapshot_month, avg_invoice_price, total_invoice_value, base_cost, surcharge, variance, currency, created_at

7. price_alerts
   - id, mat_id, supplier_id, alert_type, alert_month, previous_value, current_value, change_pct, message, status, created_at

8. ingestion_logs
   - id, table_name, row_count, status, error_message, ingested_at 

Rules:
- Only generate SELECT queries
- Use ONLY the tables listed above (DO NOT invent tables)
- Do NOT use JOIN unless necessary
- Always use backticks around column name that have space or special characters
- Always LIMIT results to 100 rows unlessuser asks for more
- If data exists in a single table, DO NOT use JOIN
- Return ONLY the SQL query with no explanation, no markdown, no backticks around the query iteself


Example:

Question: total invoice amount per supplier

SQL:
SELECT
    `Supp. ID`,
    SUM(`Total Amt`) AS total_invoice_amount
FROM invoice_data
GROUP BY `Supp. ID`
LIMIT 100;
"""

#SAFETY CHECK - blocks query that modifies the dataset
BLOCKED_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "create", "truncate", "replace", "merge", "exec", "execute", "grant", "revoke"
]

def is_safe_prompt(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    for keyword in BLOCKED_KEYWORDS:
        if keyword in prompt_lower:
            return False
    return True

def is_safe_sql(sql: str) -> bool:
    sql_lower = sql.lower().strip()
    #Must start with SELECT 
    if not sql_lower.startswith("select"):
        return False
    #must not contain any modifying keywords
    for keyword in BLOCKED_KEYWORDS:
        if keyword in sql_lower:
            return False
    return True

#GENERATE SQL FROM NATURAL LANGUAGE
def generate_sql(user_prompt: str) -> str:
    response = ollama.chat(
        model="qwen2.5:3b",
        messages=[
            {
                "role": "system",
                "content": DB_SCHEMA
            },
            {
                "role": "user",
                "content": f"Write a SQL query for: {user_prompt}"
            }
        ]
    )
    sql = response["message"]["content"].strip()

    #clean up any markdown formatting the LLM might add
    sql = sql.replace("```sql", "").replace("```", "").strip()

    return sql

#EXECUTE SQL AND RETURN DATAFRAME
def execute_sql(sql: str) -> pd.DataFrame:
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)
    return df

#GENERATE PLAIN TEXT FROM RESULT
def generate_answer(user_prompt: str, sql: str, df: pd.DataFrame) -> str:
    #Convert top 10 rows to string for context
    data_preview = df.head(10).to_string(index=False)

    response = ollama.chat(
        model="qwen2.5:3b",
        messages=[
            {
                "role": "system",
                "content": "You are a data analyst. Give a user question and query results. provide a clear and concise answer in 2-3 sentences. Do not repeat the SQL. Do not make up data."
            },
            {
                "role": "user",
                "content": f"""
Question: {user_prompt}

Query Results (top 10 rows):
{data_preview}

Total rows returned: {len(df)}

Provide a concise answer based only on the data above
"""
            }
        ]
    )
    return response["message"]["content"].strip()

#MAIN CHATBOT FUNCTION
def run_chatbot(user_prompt: str) -> dict:
    """
    Return dict with:
    - answer: plain text response
    - sql: generated SQL query
    - data: pandas dataframe of results
    - error: error message if something failed
    """

    #Step 1 : safety check on prompt
    if not is_safe_prompt(user_prompt):
        return {
            "answer": "I can only retrive data. I cannot modify, delete or create anything in database.",
            "sql": None,
            "data": None,
            "error": "Blocked Prompt"
        }
    
    try:
        #Step 2 : generate sql
        logger.info(f"Generating SQL for: {user_prompt}")
        sql = generate_sql(user_prompt)
        logger.info(f"Generated SQL: {sql}")

        #step 3 : safety check on generated sql
        if not is_safe_sql(sql):
            return {
                "answer": "I generated an unsafe query and blocked it. Please rephrase your question.",
                "sql": sql,
                "data": None,
                "error": "Unsafe SQL blocked"
            }
        
        #step 4 : execute sql
        df = execute_sql(sql)
        logger.info(f"Query returned {len(df)} rows")

        #step 5 : generate plain text answer
        answer = generate_answer(user_prompt, sql, df)

        return {
            "answer": answer,
            "sql": sql,
            "data": df,
            "error": None
        }
    
    except Exception as e:
        logger.error(f"Chatbot error: {e}")
        return {
            "answer": "Something went wrong. Please repharse your question.",
            "sql": None,
            "data": None,
            "error": str(e)
        }