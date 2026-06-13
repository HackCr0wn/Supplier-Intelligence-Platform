
import re
import os
import streamlit as st
import pandas as pd
from sqlalchemy import text
from dotenv import load_dotenv
from utils.db import get_engine

load_dotenv()
engine = get_engine()

##Schema for prompt
SCHEMA = """
Database: supplier_intelligence (MySQL 8.0)

Table: parts_master
- part_number VARCHAR(100) PK — unique part identifier
- material_from_drawing VARCHAR(500) — raw material name (e.g. LC STEEL, CAST, PA66)
- alternate_material VARCHAR(500)
- heat_treatment VARCHAR(200)
- coating VARCHAR(200)
- classification VARCHAR(100) — Ferrous/Non-Ferrous/Thermoplastic
- sub_class, family, form, grade VARCHAR
- cad_weight_lbs DECIMAL(10,4)

Table: supplier_parts
- part_number VARCHAR(100)
- supplier_id VARCHAR(100)
- supplier_name VARCHAR(200)
- plant VARCHAR(50)
- commodity VARCHAR(200)
- coo_code VARCHAR(10) — country of origin code
- coo_name VARCHAR(200)
- fx_type VARCHAR(10)
- unit_cost DECIMAL(15,4)
- snapshot_date DATE
- source_file VARCHAR(200)
- UNIQUE KEY (part_number, supplier_id, snapshot_date)

Table: surcharge_monthly
- part_number VARCHAR(100)
- invoiced_part_number VARCHAR(100)
- supplier VARCHAR(200)
- destination_plant VARCHAR(100)
- year INT, month INT
- material VARCHAR(200)
- raw_material_index VARCHAR(500)
- surcharge_weight_lbs DECIMAL(10,4)
- raw_material_base_cost DECIMAL(15,6)
- index_cost DECIMAL(15,6) — raw material index cost per lb
- part_surcharge DECIMAL(15,4) — surcharge per part
- scrap_weight_lbs DECIMAL(10,4)
- scrap_index_cost DECIMAL(15,6)
- scrap_surcharge DECIMAL(15,4)
- total_surcharge DECIMAL(15,4)
- quantity INT
- total_surcharge_cost DECIMAL(15,4) — total surcharge amount
- base_material VARCHAR(200)
- source_tier VARCHAR(5) — T1 or T2

Table: monthly_costs
- part_number VARCHAR(100)
- supplier_id VARCHAR(100)
- year INT, month INT
- month_cost DECIMAL(15,4)
- month_receipts DECIMAL(15,4)
- month_recd_spend DECIMAL(15,4)
- mrp_qty DECIMAL(15,4)
- mrp_spend DECIMAL(15,4)

Table: commodity_master
- commodity_id INT PK
- commodity_code VARCHAR(50) — LME_COPPER, LME_ALUMINIUM, MWUS_HR_COIL, etc.
- commodity_name VARCHAR(200)
- ticker_symbol VARCHAR(50) — FRED series ID

Table: commodity_prices
- commodity_id INT FK → commodity_master
- price_date DATE
- price DECIMAL(15,4)

Table: material_classification_ai
- material_from_drawing VARCHAR(500) UNIQUE
- base_material_name VARCHAR(200)
- primary_commodity VARCHAR(50)
- secondary_commodity VARCHAR(50)
- confidence DECIMAL(3,2)

Table: material_commodity_map
- material_from_drawing VARCHAR(500)
- commodity_code VARCHAR(50)
- weight DECIMAL(5,4) — weights sum to 1.0 per material
"""

SYSTEM_PROMPT = f"""You are a SQL expert for a procurement intelligence database.
Generate MySQL SELECT queries only. Never generate UPDATE, DELETE, DROP, INSERT, ALTER, or TRUNCATE.

{SCHEMA}

Rules:
1. Generate ONLY valid MySQL SELECT queries
2. Never modify data — SELECT only
3. Use backticks for column names with spaces
4. Always include LIMIT for potentially large result sets (default LIMIT 20)
5. Use ROUND() for decimal values in output
6. Return ONLY the SQL query — no explanation, no markdown code fences
7. If the question is ambiguous, make reasonable assumptions based on the schema
8. For "top" or "best" questions, ORDER BY the relevant metric DESC with LIMIT
9. For surcharge queries, use surcharge_monthly table
10. For cost queries, use monthly_costs table
11. To get commodity name, JOIN commodity_prices with commodity_master on commodity_id
"""

##Blocked keywords
BLOCKED_KEYWORDS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
                    "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE"]


def is_safe_sql(sql: str) -> bool:
    sql_upper = sql.upper().strip()
    if not sql_upper.startswith("SELECT"):
        return False
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf'\b{keyword}\b', sql_upper):
            return False
    return True


def clean_sql(raw: str) -> str:
    """Strip markdown fences, thinking tags, and extra text"""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"```sql\s*", "", cleaned)
    cleaned = re.sub(r"```\s*", "", cleaned)
    cleaned = cleaned.strip()
    # Extract first SELECT statement
    match = re.search(r"(SELECT\s.+?)(?:;|$)", cleaned, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip().rstrip(";")
    return cleaned


def call_llm(question: str) -> str:
    
    from utils.llm import call_llm
    return call_llm(SYSTEM_PROMPT, question, max_tokens=1024)

    # from groq import Groq

    # api_key = os.getenv("GROQ_API_KEY")
    # if not api_key:
    #     raise ValueError("GROQ_API_KEY not found in .env")

    # client = Groq(api_key=api_key)

    # response = client.chat.completions.create(
    #     model="qwen/qwen3-32b",
    #     messages=[
    #         {"role": "system", "content": SYSTEM_PROMPT},
    #         {"role": "user", "content": question}
    #     ],
    #     temperature=0.1,
    #     max_tokens=1024,
    # )

    # return response.choices[0].message.content


def render():
    st.markdown('<p class="main-header">SQL Chatbot</p>', unsafe_allow_html=True)

    # Check API key
    if not os.getenv("GROQ_API_KEY"):
        st.error("GROQ_API_KEY not found in .env file. Add it and restart.")
        return

    ##Chat history
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    ##Example questions
    with st.expander("Frequently Asked Questions"):
        examples = [
            "Top 10 suppliers by total surcharge cost",
            "Which materials have the highest spend in 2025?",
            "How many parts does each supplier provide?",
            "Show me average index cost by material for 2025",
            "What is the total quantity by destination plant in 2024?",
            "Which parts have surcharge weight above 10 lbs?",
            "Show me commodity prices for LME Copper in 2025",
            "Top 5 parts by annual surcharge cost in 2025",
        ]
        for ex in examples:
            if st.button(f"{ex}", key=f"ex_{ex}"):
                st.session_state.chat_input = ex

    ##Input
    default_input = st.session_state.pop("chat_input", "")
    question = st.chat_input("Ask a question about your procurement data...",
                              key="chat_main")

    if default_input and not question:
        question = default_input

    ##Display chat history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "sql" in msg:
                with st.expander("SQL Query"):
                    st.code(msg["sql"], language="sql")
            if "dataframe" in msg and msg["dataframe"] is not None:
                st.dataframe(msg["dataframe"], use_container_width=True)

    ##Process new question
    if question:
        # Add user message
        st.session_state.chat_history.append({
            "role": "user", "content": question
        })
        with st.chat_message("user"):
            st.markdown(question)

        # Generate SQL
        with st.chat_message("assistant"):
            with st.spinner("Generating SQL..."):
                try:
                    raw_sql = call_llm(question)
                    sql = clean_sql(raw_sql)

                    if not is_safe_sql(sql):
                        error_msg = "Blocked: Only SELECT queries are allowed."
                        st.error(error_msg)
                        st.session_state.chat_history.append({
                            "role": "assistant", "content": error_msg
                        })
                        return

                    # Show SQL
                    with st.expander("Generated SQL", expanded=True):
                        st.code(sql, language="sql")

                    # Execute
                    with st.spinner("⚡ Running query..."):
                        df = pd.read_sql(text(sql), engine)

                    if df.empty:
                        result_msg = "Query executed but returned no results."
                        st.info(result_msg)
                        st.session_state.chat_history.append({
                            "role": "assistant",
                            "content": result_msg,
                            "sql": sql,
                            "dataframe": None
                        })
                    else:
                        result_msg = f"{len(df)} rows returned"
                        st.success(result_msg)
                        st.dataframe(df, use_container_width=True)

                        # Download button
                        csv = df.to_csv(index=False)
                        st.download_button("Download CSV", csv,
                                          "query_result.csv", "text/csv")

                        st.session_state.chat_history.append({
                            "role": "assistant",
                            "content": result_msg,
                            "sql": sql,
                            "dataframe": df
                        })

                except Exception as e:
                    error_msg = f"Error: {str(e)}"
                    st.error(error_msg)
                    st.session_state.chat_history.append({
                        "role": "assistant", "content": error_msg
                    })

    ##Sidebar info
    # st.sidebar.markdown("---")
    # st.sidebar.markdown("**Chatbot Info**")
    # st.sidebar.caption("Model: qwen3-32b via Groq")
    # st.sidebar.caption("Speed: ~500 tokens/sec")
    # st.sidebar.caption("Safety: SELECT only, no data modification")
