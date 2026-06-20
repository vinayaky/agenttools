import os
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_community.utilities import SerpAPIWrapper
from apikey import apikey

import smtplib
from email.message import EmailMessage
import ssl
import requests
from bs4 import BeautifulSoup
import urllib.parse


os.environ['OPENAI_API_KEY'] = apikey
os.environ['OPENAI_BASE_URL'] = 'https://openrouter.ai/api/v1'
os.environ['SERPAPI_API_KEY']='2c0b2505e505644a2c43da7a76055c48344c7cf4078f5f09b34fb1e6a60555e8'  # kept for potential future use


import sys
if len(sys.argv) > 1:
    prompt = ' '.join(sys.argv[1:])
else:
    prompt=input('Enter your prompt: ')





def send_email(info):
    import json
    data = json.loads(info)
    es='vinayak2072005@gmail.com'
    email_password="odkqyjagrrflodev"
    er=data.get("Emailaddress","")
    subject=data.get("subject","")
    message=data.get("body","")
    if not er:
        return "Error: No recipient email address provided"
    email_message=EmailMessage()
    email_message["From"]=es
    email_message["To"]=er
    email_message["Subject"]=subject
    email_message.set_content(message)
    context=ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com',465, context=context, timeout=30) as smtp:
            smtp.login(es,email_password)
            smtp.send_message(email_message)
        return 'Email sent successfully'
    except Exception as e:
        return f'Failed to send email: {e}'





    
#pdfdounloader

def create_directory(directory_path):
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        print(f"Directory created: {directory_path}")
    else:
        print("Directory already exists.")

def download_pdf(url):
    response = requests.get(url, timeout=30)
    print(url, flush=True)
    if response.status_code == 200:
        filename = url.split("/")[-1].split("?")[0]
        directory_path = os.path.join(os.path.expanduser("~"), "Downloads")
        create_directory(directory_path)
        save_path = os.path.join(directory_path, filename)
        with open(save_path, "wb") as file:
            file.write(response.content)
        return "PDF downloaded successfully!"
    return f"Failed to download PDF, status: {response.status_code}"

def search_and_download_pdf(topic):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    encoded_topic = urllib.parse.quote_plus(topic)
    search_url = f"https://scholar.google.com/scholar?hl=en&as_sdt=0%2C5&q={encoded_topic}"
    response = requests.get(search_url, headers=headers, timeout=20)
    soup = BeautifulSoup(response.text[:200000], "html.parser")
    pdf_links = [a.get("href") for a in soup.select("a[href]") if ".pdf" in a.get("href","").lower()]
    if not pdf_links:
        return "No PDFs found for the given topic."
    full_pdf_url = urllib.parse.urljoin(search_url, pdf_links[0])
    return download_pdf(full_pdf_url)



search = SerpAPIWrapper()
def search_with_timeout(query):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        f = pool.submit(search.run, query)
        return f.result(timeout=15)


tools = [
    Tool.from_function(
        name="Email_sending_tool",
        func=lambda message: send_email(message),
        description='Useful for sending emails. Input must be a JSON object with three keys: "Emailaddress" (recipient email), "subject" (email subject), and "body" (email body text).',
    ),
    Tool.from_function(
        name="Pdf_download_tool",
        func=search_and_download_pdf,
        description="useful for when you need to download a pdf file. Provide the topic of the PDF the user wants to download.",
    ),
    Tool(
        name="current_search",
        func=search_with_timeout,
        description="useful for when you need to answer questions about current events or the current state of the world"
    )
    
]


llm= ChatOpenAI(temperature=0, model="gpt-4o-mini")


agent= create_react_agent(llm, tools)
if prompt:
    result = agent.invoke({"messages": [("human", prompt)]})
    for msg in result["messages"]:
        if msg.type == "ai" and msg.content:
            print(msg.content)
