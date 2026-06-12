import os 
from dotenv import load_dotenv 

load_dotenv("/opt/ai-support-agent/.env") 

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2") 

ASTERISK_ARI_URL = os.getenv("ASTERISK_ARI_URL") 
ASTERISK_ARI_USER = os.getenv("ASTERISK_ARI_USER") 
ASTERISK_ARI_PASSWORD = os.getenv("ASTERISK_ARI_PASSWORD") 
ASTERISK_ARI_APP = os.getenv("ASTERISK_ARI_APP", "ai_support") 

ZAMMAD_URL = os.getenv("ZAMMAD_URL") 
ZAMMAD_TOKEN = os.getenv("ZAMMAD_TOKEN") 

CSV_USERS_FILE = os.getenv("CSV_USERS_FILE")
