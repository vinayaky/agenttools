import streamlit as st
import os, sys, json, sqlite3, smtplib, ssl, requests, urllib.parse, concurrent.futures, hashlib, time, datetime
from email.message import EmailMessage
from bs4 import BeautifulSoup
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_community.utilities import SerpAPIWrapper
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from apikey import apikey

# ─── Config ───────────────────────────────────────────────────────
st.set_page_config(page_title="AI Agent Dashboard", layout="wide", initial_sidebar_state="expanded")
PAGE_SIZE = 20
DB_PATH = os.path.join(os.path.dirname(__file__), "dashboard.db")
os.environ['SERPAPI_API_KEY'] = '2c0b2505e505644a2c43da7a76055c48344c7cf4078f5f09b34fb1e6a60555e8'

# ─── Database ─────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                username TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                role TEXT NOT NULL,
                content TEXT,
                tool_used TEXT,
                duration_ms REAL,
                tokens INTEGER,
                status TEXT DEFAULT 'ok',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES('model','openai/gpt-4o-mini')")
        conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES('temperature','0.0')")
        conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES('max_tokens','1024')")
        conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES('local_username','admin')")
        conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES('local_password','admin')")
    load_config()

def load_config():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    st.session_state.cfg = {k: v for k, v in rows}

def save_config(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO config(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)", (key, str(value)))
    load_config()

def record_metric(agent_name, metric_name, metric_value):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO metrics(agent_name,metric_name,metric_value) VALUES(?,?,?)",
                     (agent_name, metric_name, metric_value))

def get_or_create_session(agent_name, username="default"):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE agent_name=? AND username=? ORDER BY id DESC LIMIT 1",
            (agent_name, username)).fetchone()
        if row:
            return row[0]
        cur = conn.execute("INSERT INTO sessions(agent_name,username) VALUES(?,?)", (agent_name, username))
        return cur.lastrowid

def save_message(session_id, role, content, tool_used=None, duration_ms=None, tokens=None, status="ok"):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages(session_id,role,content,tool_used,duration_ms,tokens,status) VALUES(?,?,?,?,?,?,?)",
            (session_id, role, content, tool_used, duration_ms, tokens, status))
        conn.execute("UPDATE sessions SET message_count=message_count+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))

def query_df(sql, params=()):
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(sql, conn, params=params)

# ─── Auth ─────────────────────────────────────────────────────────┐
def login_page():
    st.markdown("<h1 style='text-align:center;margin-top:120px'>AI Agent Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;color:#888'>Sign in with Google to continue</p>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.login("google")

        # Dev fallback when Google OAuth not configured
        st.markdown("---")
        with st.expander("Or use local login (dev mode)"):
            with st.form("dev_login"):
                user = st.text_input("Username", value="admin")
                pw = st.text_input("Password", type="password")
                if st.form_submit_button("Login", use_container_width=True, type="primary"):
                    cfg_username = st.session_state.get("cfg", {}).get("local_username", "admin")
                    cfg_password = st.session_state.get("cfg", {}).get("local_password", "admin")
                    if user == cfg_username and pw == cfg_password:
                        st.session_state.user = {"username": user, "role": "admin", "email": f"{user}@local.dev"}
                        st.rerun()
                    else:
                        st.error("Invalid credentials")

# ─── Tools & Agents ───────────────────────────────────────────────
def build_tools_and_agents():
    def send_email(info):
        data = json.loads(info)
        es, pw = 'vinayak2072005@gmail.com', "odkqyjagrrflodev"
        er, subj, body = data.get("Emailaddress",""), data.get("subject",""), data.get("body","")
        if not er:
            return "Error: No recipient email"
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = es, er, subj
        msg.set_content(body)
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=30) as s:
                s.login(es, pw)
                s.send_message(msg)
            return "Email sent successfully"
        except Exception as e:
            return f"Failed: {e}"

    srch = SerpAPIWrapper()
    def web_search(q):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(srch.run, q).result(timeout=15)

    def pdf_search(topic):
        hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        enc = urllib.parse.quote_plus(topic)
        r = requests.get(f"https://scholar.google.com/scholar?hl=en&as_sdt=0%2C5&q={enc}", headers=hdrs, timeout=20)
        soup = BeautifulSoup(r.text[:200000], "html.parser")
        pdfs = [a.get("href") for a in soup.select("a[href]") if ".pdf" in a.get("href","").lower()]
        if not pdfs:
            return "No PDFs found."
        resp = requests.get(urllib.parse.urljoin(r.url, pdfs[0]), timeout=30)
        if resp.status_code == 200:
            name = resp.url.split("/")[-1].split("?")[0]
            path = os.path.join(os.path.expanduser("~"), "Downloads", name)
            with open(path, "wb") as f:
                f.write(resp.content)
            return f"Downloaded to {path}"
        return f"Failed ({resp.status_code})"

    cfg = st.session_state.get("cfg", {})
    llm_kwargs = dict(
        model=cfg.get("model", "openai/gpt-4o-mini"),
        api_key=apikey,
        base_url="https://openrouter.ai/api/v1",
        temperature=float(cfg.get("temperature", 0)),
        max_tokens=int(cfg.get("max_tokens", 1024)),
    )
    return {
        "Email Agent": create_react_agent(ChatOpenAI(**llm_kwargs), [
            Tool.from_function(name="Email_sending_tool", func=lambda m: send_email(m),
                description='Send emails. Input: JSON with "Emailaddress", "subject", "body"'),
        ]),
        "PDF Agent": create_react_agent(ChatOpenAI(**llm_kwargs), [
            Tool.from_function(name="Pdf_download_tool", func=pdf_search,
                description="Search and download a PDF on a given topic from Google Scholar."),
        ]),
        "Web Search Agent": create_react_agent(ChatOpenAI(**llm_kwargs), [
            Tool(name="current_search", func=web_search,
                description="Search the web for current events or general knowledge questions."),
        ]),
    }

# ─── Pages ─────────────────────────────────────────────────────────

def page_overview():
    st.header("Overview")
    with sqlite3.connect(DB_PATH) as conn:
        total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        today_msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE date(created_at)=date('now')").fetchone()[0]
        ok_count = conn.execute("SELECT COUNT(*) FROM messages WHERE status='ok'").fetchone()[0]
        err_count = conn.execute("SELECT COUNT(*) FROM messages WHERE status!='ok'").fetchone()[0]
        avg_dur = conn.execute("SELECT COALESCE(AVG(duration_ms),0) FROM messages").fetchone()[0]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Requests", f"{total_msgs:,}", f"+{today_msgs} today")
    col2.metric("Success Rate", f"{ok_count/max(total_msgs,1)*100:.0f}%", f"{err_count} errors")
    col3.metric("Avg Response", f"{avg_dur/1000:.2f}s")
    col4.metric("Active Sessions", query_df("SELECT COUNT(DISTINCT session_id) FROM messages WHERE created_at > datetime('now','-1 hour')").iloc[0,0])

    st.markdown("---")
    col_a, col_b = st.columns(2)
    with col_a:
        df = query_df("""
            SELECT strftime('%H:00',created_at) as hour, COUNT(*) as count
            FROM messages WHERE created_at > datetime('now','-24 hours')
            GROUP BY hour ORDER BY hour
        """)
        if not df.empty:
            fig = px.line(df, x="hour", y="count", title="Requests (Last 24h)", markers=True)
            fig.update_layout(height=280, margin=dict(l=20,r=20,t=30,b=20))
            st.plotly_chart(fig, use_container_width=True)

    with col_b:
        df2 = query_df("""
            SELECT s.agent_name, COUNT(*) as count FROM messages m
            JOIN sessions s ON m.session_id=s.id
            GROUP BY s.agent_name ORDER BY count DESC
        """)
        if not df2.empty:
            fig = px.pie(df2, names="agent_name", values="count", title="Tool Usage", hole=0.4)
            fig.update_layout(height=280, margin=dict(l=20,r=20,t=30,b=20))
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("Recent Activity")
    df3 = query_df("""
        SELECT m.created_at, s.agent_name, m.role, substr(m.content,1,80) as content,
               m.tool_used, m.duration_ms, m.status
        FROM messages m JOIN sessions s ON m.session_id=s.id
        ORDER BY m.id DESC LIMIT 10
    """)
    if not df3.empty:
        df3["duration_ms"] = df3["duration_ms"].apply(lambda x: f"{x/1000:.2f}s" if x else "")
        st.dataframe(df3, use_container_width=True, hide_index=True,
                     column_config={"created_at": "Time", "agent_name": "Agent", "role": "Role",
                                    "content": "Message", "tool_used": "Tool", "duration_ms": "Dur", "status": "Status"})

def page_chat():
    st.header("Agent Chat")
    selected = st.selectbox("Select Agent", ["Email Agent", "PDF Agent", "Web Search Agent"], key="chat_agent")
    agent_key = f"chat_msgs_{selected}"
    if agent_key not in st.session_state:
        st.session_state[agent_key] = []

    agents = st.session_state.agents
    chat_msgs = st.session_state[agent_key]

    # Session management
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("Clear Chat", use_container_width=True):
            st.session_state[agent_key] = []
            st.rerun()

    for msg in chat_msgs:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("meta"):
                st.caption(f"⏱ {msg['meta'].get('duration','')}  |  tokens: {msg['meta'].get('tokens','')}")

    if prompt := st.chat_input(f"Ask {selected}..."):
        chat_msgs.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        sid = get_or_create_session(selected)
        t0 = time.time()
        status_ = "ok"
        try:
            result = agents[selected].invoke({"messages": [("human", prompt)]})
            reply = next((m.content for m in result["messages"] if m.type == "ai" and m.content), "")
        except Exception as e:
            reply = f"Error: {e}"
            status_ = "error"
        dur = (time.time() - t0) * 1000
        tokens = len(prompt.split()) + len(reply.split())
        record_metric(selected, "response_time_ms", dur)

        save_message(sid, "user", prompt, duration_ms=dur, tokens=tokens, status=status_)
        # Extract tool used
        tool_used = None
        for m in result.get("messages", []):
            if m.type == "ai" and m.tool_calls:
                tool_used = m.tool_calls[0].get("name","") if isinstance(m.tool_calls, list) else ""
            elif m.type == "tool":
                tool_used = getattr(m, "name", tool_used)
        save_message(sid, "assistant", reply, tool_used=tool_used, duration_ms=dur, tokens=tokens, status=status_)

        chat_msgs.append({"role": "assistant", "content": reply, "meta": {"duration": f"{dur/1000:.2f}s", "tokens": tokens}})
        with st.chat_message("assistant"):
            st.markdown(reply)
            st.caption(f"⏱ {dur/1000:.2f}s  |  tokens: {tokens}")

def page_analytics():
    st.header("Analytics & Metrics")
    period = st.radio("Period", ["24h", "7d", "30d"], horizontal=True)
    hours = {"24h": 24, "7d": 168, "30d": 720}[period]

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(f"""
            SELECT strftime('%Y-%m-%d %H:00', created_at) as bucket,
                   COUNT(*) as requests,
                   COALESCE(AVG(duration_ms),0) as avg_dur,
                   SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END)*1.0/COUNT(*)*100 as success_rate
            FROM messages
            WHERE created_at > datetime('now','-{hours} hours')
            GROUP BY bucket ORDER BY bucket
        """, conn)

    if not df.empty:
        col1, col2 = st.columns(2)
        with col1:
            fig = px.line(df, x="bucket", y="requests", title="Requests Over Time", markers=True)
            fig.update_layout(height=300, margin=dict(l=20,r=20,t=30,b=20))
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            fig = px.area(df, x="bucket", y="avg_dur", title="Avg Response Time (ms)")
            fig.update_layout(height=300, margin=dict(l=20,r=20,t=30,b=20))
            st.plotly_chart(fig, use_container_width=True)

        col3, col4 = st.columns(2)
        with col3:
            fig = px.bar(df, x="bucket", y="success_rate", title="Success Rate (%)",
                         color="success_rate", color_continuous_scale="RdYlGn")
            fig.update_layout(height=300, margin=dict(l=20,r=20,t=30,b=20))
            st.plotly_chart(fig, use_container_width=True)
        with col4:
            df2 = query_df(f"""
                SELECT s.agent_name, AVG(m.duration_ms) as avg_dur
                FROM messages m JOIN sessions s ON m.session_id=s.id
                WHERE m.created_at > datetime('now','-{hours} hours')
                GROUP BY s.agent_name
            """)
            fig = px.bar(df2, x="agent_name", y="avg_dur", title="Avg Response by Agent",
                         color="agent_name")
            fig.update_layout(height=300, margin=dict(l=20,r=20,t=30,b=20), showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data yet. Start chatting to see analytics.")

    # Token usage chart
    st.markdown("---")
    st.subheader("Token Usage & Cost")
    df_tokens = query_df(f"""
        SELECT strftime('%Y-%m-%d', created_at) as day, SUM(tokens) as total_tokens
        FROM messages WHERE role='assistant'
        AND created_at > datetime('now','-{hours} hours')
        GROUP BY day ORDER BY day
    """)
    if not df_tokens.empty:
        df_tokens["est_cost"] = df_tokens["total_tokens"] * 0.00015 / 1000
        fig = px.bar(df_tokens, x="day", y="total_tokens", title="Daily Token Usage")
        fig.update_layout(height=280, margin=dict(l=20,r=20,t=30,b=20))
        st.plotly_chart(fig, use_container_width=True)
        total_cost = df_tokens["est_cost"].sum()
        st.metric("Estimated Cost (USD)", f"${total_cost:.4f}")

def page_logs():
    st.header("Logs & History")
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        agent_filter = st.selectbox("Agent", ["All", "Email Agent", "PDF Agent", "Web Search Agent"])
    with col_f2:
        status_filter = st.selectbox("Status", ["All", "ok", "error"])
    with col_f3:
        date_filter = st.date_input("From Date", datetime.date.today() - datetime.timedelta(days=7))

    q = """
        SELECT m.id, m.created_at, s.agent_name, m.role, m.content, m.tool_used,
               m.duration_ms, m.tokens, m.status
        FROM messages m JOIN sessions s ON m.session_id=s.id
        WHERE date(m.created_at) >= ?
    """
    params = [date_filter.isoformat()]
    if agent_filter != "All":
        q += " AND s.agent_name=?"
        params.append(agent_filter)
    if status_filter != "All":
        q += " AND m.status=?"
        params.append(status_filter)
    q += " ORDER BY m.id DESC"

    df = query_df(q, params)
    if df.empty:
        st.info("No matching records.")
        return

    page_num = st.session_state.get("log_page", 0)
    total_pages = max(1, len(df) // PAGE_SIZE)
    col_p1, col_p2, col_p3 = st.columns([1, 2, 1])
    with col_p2:
        st.markdown(f"**{len(df)} records**  ·  Page {page_num+1}/{total_pages+1}")
    offset = page_num * PAGE_SIZE
    page_df = df.iloc[offset:offset+PAGE_SIZE].copy()
    page_df["duration_ms"] = page_df["duration_ms"].apply(lambda x: f"{x/1000:.2f}s" if pd.notna(x) else "")

    st.dataframe(page_df, use_container_width=True, hide_index=True,
                 column_config={
                     "id": None, "created_at": "Time", "agent_name": "Agent", "role": "Role",
                     "content": st.column_config.TextColumn("Message", width="large"),
                     "tool_used": "Tool", "duration_ms": "Dur", "tokens": "Tokens", "status": "Status"
                 })

    col_p1, col_p2, col_p3 = st.columns([1, 2, 1])
    with col_p1:
        if st.button("← Prev", disabled=(page_num == 0), use_container_width=True):
            st.session_state.log_page = page_num - 1
            st.rerun()
    with col_p3:
        if st.button("Next →", disabled=(offset + PAGE_SIZE >= len(df)), use_container_width=True):
            st.session_state.log_page = page_num + 1
            st.rerun()

    csv = df.to_csv(index=False).encode()
    st.download_button("Export CSV", data=csv, file_name="agent_logs.csv", mime="text/csv")

def page_config():
    st.header("Configuration")

    with st.form("config_form"):
        col1, col2 = st.columns(2)
        with col1:
            model = st.selectbox("Model", ["openai/gpt-4o-mini", "openai/gpt-4o", "anthropic/claude-3.5-sonnet",
                                           "google/gemini-pro", "mistral/mistral-large"],
                                 index=["openai/gpt-4o-mini", "openai/gpt-4o", "anthropic/claude-3.5-sonnet",
                                        "google/gemini-pro", "mistral/mistral-large"].index(
                                     st.session_state.cfg.get("model", "openai/gpt-4o-mini")))
            temperature = st.slider("Temperature", 0.0, 2.0, float(st.session_state.cfg.get("temperature", 0)), 0.1)
        with col2:
            max_tokens = st.number_input("Max Tokens", 128, 8192, int(st.session_state.cfg.get("max_tokens", 1024)), 128)
            if st.form_submit_button("Save Configuration", type="primary", use_container_width=True):
                save_config("model", model)
                save_config("temperature", str(temperature))
                save_config("max_tokens", str(max_tokens))
                st.success("Saved! Agents will use new config on next interaction.")

    st.markdown("---")
    st.subheader("Tool Toggles")
    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1:
        st.checkbox("Email Agent", value=True, disabled=True, help="Always enabled")
    with col_t2:
        st.checkbox("PDF Agent", value=True, disabled=True)
    with col_t3:
        st.checkbox("Web Search Agent", value=True, disabled=True)

    st.markdown("---")
    st.subheader("API Keys")
    st.text_input("OpenAI / OpenRouter Key", value=apikey[:10]+"..."+apikey[-4:], disabled=True)
    st.text_input("SERPAPI Key", value="2c0b2505...", disabled=True)

    st.markdown("---")
    st.subheader("Database Stats")
    with sqlite3.connect(DB_PATH) as conn:
        for table, label in [("sessions","Sessions"), ("messages","Messages"), ("metrics","Metric Records")]:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            st.metric(label, cnt)

    if st.button("Reset All Data", type="secondary", use_container_width=True):
        with sqlite3.connect(DB_PATH) as conn:
            conn.executescript("DELETE FROM metrics; DELETE FROM messages; DELETE FROM sessions; VACUUM;")
        st.success("All data reset.")
        st.rerun()

# ─── Main App ──────────────────────────────────────────────────────

def main():
    init_db()
    load_config()

    # Google OAuth login
    if st.user.is_logged_in:
        st.session_state.user = {
            "username": st.user.get("email", "user").split("@")[0],
            "email": st.user.get("email", ""),
            "name": st.user.get("name", ""),
            "role": "admin",
        }
    elif not st.session_state.get("user"):
        login_page()
        return

    # Rebuild agents on config change
    if "agents" not in st.session_state:
        st.session_state.agents = build_tools_and_agents()

    with st.sidebar:
        user = st.session_state.user
        if user.get("name"):
            st.markdown(f"**{user['name']}**  \n{user.get('email','')}")
        else:
            st.markdown(f"**{user['username']}** ({user['role']})")
        st.markdown("---")
        page = st.radio("Navigation", ["Overview", "Agent Chat", "Analytics"],
                        format_func=lambda x: f"📊 {x}" if x=="Overview" else
                                     f"💬 {x}" if x=="Agent Chat" else
                                     f"📈 {x}")
        st.markdown("---")
        with sqlite3.connect(DB_PATH) as conn:
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            today = conn.execute("SELECT COUNT(*) FROM messages WHERE date(created_at)=date('now')").fetchone()[0]
        st.metric("Total Messages", total, today)
        st.markdown("---")
        if st.button("Logout", use_container_width=True):
            st.logout()
            st.session_state.user = None
            st.rerun()

    pages = {"Overview": page_overview, "Agent Chat": page_chat, "Analytics": page_analytics}
    pages[page]()

if __name__ == "__main__":
    main()
