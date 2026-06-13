from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import text
from utils.db import get_engine
from chatbot.nl_to_sql import run_chatbot
import pandas as pd

app = Flask(__name__)
CORS(app)
engine = get_engine()

#HELPER
def query_to_json(sql):
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)
    return df.to_dict(orient="records")

#ROUTE
@app.route("/app/kpis")
def kpis():
    with engine.connect() as conn:
        open_alerts = conn.execute(text("SELECT COUNT(*) FROM price_alerts WHERE status='OPEN'")).scalar()
        resolved_alerts = conn.execute(text("SELECT COUNT(*) FROM price_alerts WHERE status='RESOLVED")).scalar()
        total_materials = conn.execute(text("SELECT COUNT(DISTINCT mat_id) FROM price_snapshots")).scalar()
        total_suppliers = conn.execute(text("SELECT COUNT(DISTINCT supplier_id) FROM price_snapshots")).scalar()
    return jsonify({
        "open_alerts": open_alerts,
        "resolved_alerts": resolved_alerts,
        "total_materias": total_materials,
        "total_suppliers": total_suppliers
    })

@app.route("/api/top5_changes")
def top5_changes():
    data = query_to_json("""
        SELECT mat_id, supplier_id, alert_type, change_pct, message, alert_month
        FROM price_alerts
        ORDER BY change_pct DESC
        LIMIT 5
    """)
    return jsonify(data)

@app.route("/api/alert_breakdown")
def alert_breakdown():
    data = query_to_json("""
        SELECT alert_type, COUNT(*) as count
        FROM price_alerts
        GROUP BY alert_type
    """)
    return jsonify(data)

@app.route("/api/alerts")
def alerts():
    status = request.args.get("status", "ALL")
    if status == "ALL":
        sql = "SELECT * FROM price_alerts ORDER BY created_at DESC LIMIT 100"
    else:
        sql = f"SELECT * FROM price_alerts WHERE status='{status}' ORDER BY created_at DESC LIMIT 100"
    return jsonify(query_to_json(sql))

@app.route("/api/ingestion_logs")
def ingestion_logs():
    data = query_to_json("""
        SELECT * FROM ingestion_logs
        ORDER BY ingested_at DESC
        LIMIT 20
    """)
    return jsonify(data)

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    prompt = data.get("prompt", "")
    result = run_chatbot(prompt)
    return jsonify({
        "answer": result["answer"],
        "sql": result["sql"],
        "data": result["data"].to_dict(orient="records") if result["data"] is not None else []
    })

if __name__ == "__main__":
    app.run(debug=False, port=5000)