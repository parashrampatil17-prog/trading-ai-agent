TRADING AI - DASHBOARD READ-ONLY INTEGRATION STAGE

या पॅकेजमध्ये:
- app_dashboard_candidate.py: secure dashboard candidate मध्ये authenticated execution status, read-only reconciliation, intent आणि exit-watch APIs जोडले आहेत.
- live_order_manager.py, broker_reconciliation.py, auto_execution_guard.py: आधीचे fail-closed staging modules.
- test_*.py: module-level tests.

नवीन API:
GET  /api/execution/status
POST /api/execution/reconcile  (login + X-CSRF-Token आवश्यक)
GET  /api/execution/intents
GET  /api/execution/exit-watches

महत्त्वाचे:
- या पॅकेजमध्ये Kite place_order/modify_order/cancel_order नाही.
- Reconciliation फक्त Zerodha orders आणि net positions वाचते.
- mismatch किंवा broker API failure आल्यास persistent Kill Switch HALT होतो.
- app_dashboard_candidate.py हा स्वतंत्र candidate आहे; मूळ app.py overwrite करू नका.
- Flask/KiteConnect dependencies असलेल्या Render environment मध्येच dashboard runtime test करा.
- Render वर TRADING_AI_DB_PATH साठी persistent disk path द्या; अन्यथा restart/deploy नंतर SQLite data हरवू शकतो.
- Live execution बंदच आहे. TRADING_AI_LIVE_ENABLED=true करू नका.
