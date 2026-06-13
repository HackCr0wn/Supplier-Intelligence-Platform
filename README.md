# Supplier-Intelligence-Platform
AI-powered supplier analytics platform with price forecasting, TLC calculator, and a natural language SQL chatbot.

# Features
-Market Index — Compare supplier prices vs commodity benchmarks
-TLC Calculator — Total Landed Cost breakdown per part
-Forecasting — Commodity price prediction using ETS, LSTM, BiLSTM
-Chatbot — Ask procurement questions in plain English (NL to SQL)
-Upload — Drop Excel files, AI maps and loads data automatically
-Alerts — Price change alerts with severity levels

# Tech Stack
- **Frontend: Streamlit
- Backend: Python
- Backend: MySQL
- ML Models: ETS, LSTM, BiLSTM
- LLM: NVIDIA NIM API
- commodity data: FRED API

# Setup
See [SETUP_GUIDE.md](SETUP_GUIDE.md) for full local setup instructions.

# Environment Variable
Create a `.env` file with:
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=procurement_system
FRED_API_KEY=your_key
NVIDIA_API_KEY=your_key

# Note
This is a Live system deployment that uses sample data.
