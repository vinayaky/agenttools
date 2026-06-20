import streamlit as st
import os, sys, json, smtplib, ssl, requests, urllib.parse, concurrent.futures
from email.message import EmailMessage
from bs4 import BeautifulSoup
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_community.utilities import SerpAPIWrapper
sys.path.insert(0, os.path.dirname(__file__))
from apikey import apikey

os.environ['OPENAI_API_KEY'] = apikey
os.environ['SERPAPI_API_KEY'] = '2c0b2505e505644a2c43da7a76055c48344c7cf4078f5f09b34fb1e6a60555e8'

st.set_page_config(page_title="AI Multi-Agent Hub", layout="centered")
st.title("AI Multi-Agent Hub")

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

search = SerpAPIWrapper()
def web_search(q):
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(search.run, q).result(timeout=15)

def pdf_search(topic):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    encoded = urllib.parse.quote_plus(topic)
    url = f"https://scholar.google.com/scholar?hl=en&as_sdt=0%2C5&q={encoded}"
    r = requests.get(url, headers=headers, timeout=20)
    soup = BeautifulSoup(r.text[:200000], "html.parser")
    pdfs = [a.get("href") for a in soup.select("a[href]") if ".pdf" in a.get("href","").lower()]
    if not pdfs:
        return "No PDFs found."
    full = urllib.parse.urljoin(url, pdfs[0])
    resp = requests.get(full, timeout=30)
    if resp.status_code == 200:
        name = full.split("/")[-1].split("?")[0]
        path = os.path.join(os.path.expanduser("~"), "Downloads", name)
        with open(path, "wb") as f:
            f.write(resp.content)
        return f"Downloaded to {path}"
    return f"Failed ({resp.status_code})"

llm_kwargs = dict(model="openai/gpt-4o-mini", api_key=apikey, base_url="https://openrouter.ai/api/v1")

agents = {
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

if "messages" not in st.session_state:
    st.session_state.messages = {name: [] for name in agents}

selected = st.sidebar.radio("Select Agent", list(agents.keys()))
st.sidebar.markdown("---")
for name, desc in [("Email Agent", "Send emails via Gmail"), ("PDF Agent", "Search & download PDFs"), ("Web Search Agent", "Search the web")]:
    st.sidebar.markdown(f"**{name}**  \n{desc}")

st.subheader(selected)
chat_msgs = st.session_state.messages[selected]
for msg in chat_msgs:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input(f"Ask {selected}..."):
    chat_msgs.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = agents[selected].invoke({"messages": [("human", prompt)]})
            reply = next((m.content for m in result["messages"] if m.type == "ai" and m.content), "")
            st.markdown(reply)
            chat_msgs.append({"role": "assistant", "content": reply})
