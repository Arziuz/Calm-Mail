import os
import re
import json
import time
import ollama
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- CONFIGURATION ---
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
CONFIG_FILE = 'config.json'

def load_config():
    """Loads the user configuration file."""
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ Error: {CONFIG_FILE} not found.")
        exit()
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def get_gmail_service():
    """Authenticates and returns the Gmail API service."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

class LabelManager:
    def __init__(self, service):
        self.service = service
        self.cache = {}
        self.refresh_cache()

    def refresh_cache(self):
        """Downloads all current Gmail labels to minimize API calls."""
        try:
            results = self.service.users().labels().list(userId='me').execute()
            self.cache = {l['name'].lower(): l['id'] for l in results.get('labels', [])}
        except HttpError as e:
            print(f"⚠️ Error fetching labels: {e}")

    def get_or_create(self, label_name):
        """Returns ID of a label; creates it if it doesn't exist."""
        clean_name = label_name.strip()
        
        # Handle System Labels mapping
        system_labels = {"INBOX", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT"}
        if clean_name.upper() in system_labels:
            return clean_name.upper()
        
        key = clean_name.lower()
        
        # 1. Check Cache
        if key in self.cache:
            return self.cache[key]

        # 2. Create New Label if missing
        print(f"   [SYSTEM] Creating New Label: '{clean_name}'")
        try:
            label = self.service.users().labels().create(userId='me', body={
                "name": clean_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show"
            }).execute()
            
            # Update cache
            new_id = label['id']
            self.cache[key] = new_id
            return new_id
            
        except HttpError as error:
            if error.resp.status == 409: # Already exists (race condition)
                self.refresh_cache()
                return self.cache.get(key)
            print(f"   [ERROR] Could not create label {clean_name}: {error}")
            return None

def extract_domain_info(sender_str):
    """Parses 'Name <email@domain.com>' into components."""
    match = re.search(r'<(.+?)>', sender_str)
    email = match.group(1) if match else sender_str
    try:
        domain = email.split('@')[-1].lower().strip()
        return email, domain
    except:
        return email, "unknown"

def analyze_email_sovereign(email_data, config):
    """
    The Logic Core:
    1. Kill List (Phase 0)
    2. White List (Phase 1)
    3. AI Tribunal (Phase 2)
    """
    sender = email_data['sender']
    clean_email, domain = extract_domain_info(sender)
    
    # --- PHASE 0: THE KILL LIST (Hard Delete) ---
    for black_domain in config.get('blacklist_domains', []):
        if black_domain in domain:
            return {"action": "DELETE", "reason": f"Blacklisted Domain ({domain})"}

    # --- PHASE 1: THE VIP LIST (Family) ---
    for family_email in config.get('family_emails', []):
        if family_email.lower() in clean_email.lower():
            return {"action": "LABEL", "label": "Family", "reason": "Whitelist"}

    # --- PHASE 2: AI TRIBUNAL ---
    prompt = f"""
    Analyze this email. Trust the SENDER DOMAIN above the subject.
    
    METADATA:
    - From: {clean_email} (Domain: {domain})
    - Subject: {email_data['subject']}
    - Snippet: {email_data['snippet']}
    
    YOUR LABELS: {json.dumps(config['fixed_labels'])}
    
    CRITICAL RULES:
    1. CHECK THE SENDER DOMAIN FIRST. 
       - "Government" is ONLY for official domains (.gov, .gc.ca).
       - Quora/Reddit/Social notifications are DELETE or SOCIAL.
    2. "Finance" is for bills/banks.
    3. If it's useless promo/spam, output "DELETE".
    4. If it doesn't fit a known label, INVENT a short 1-word label (e.g. "Gaming", "Medical").
    
    Output valid JSON only:
    {{
      "reasoning": "Sender is X, Subject is Y...",
      "category": "LabelName" or "DELETE"
    }}
    """
    
    try:
        response = ollama.chat(model=config['model'], messages=[
            {'role': 'system', 'content': 'You are an email sorting agent. Output JSON only.'},
            {'role': 'user', 'content': prompt}
        ])
        content = response['message']['content']
        
        # Robust JSON Extraction
        start = content.find('{')
        end = content.rfind('}') + 1
        if start == -1: raise ValueError("No JSON found in response")
        
        decision = json.loads(content[start:end])
        category = decision.get('category', 'INBOX').strip()
        
        # Normalize 'DELETE' variations
        if category.upper() in ["DELETE", "SPAM", "TRASH"]:
            return {"action": "DELETE", "reason": decision.get('reasoning')}
            
        return {"action": "LABEL", "label": category, "reason": decision.get('reasoning')}

    except Exception as e:
        # Fallback: If AI fails, leave in Inbox
        print(f"   ⚠️ AI Error: {e}")
        return {"action": "SKIP"}

def main():
    config = load_config()
    service = get_gmail_service()
    label_manager = LabelManager(service)
    
    print(f"\n=== ARZIUS SOVEREIGN AGENT v3.0 ===")
    print(f"Model: {config['model']}")
    print(f"Blacklist: {len(config.get('blacklist_domains', []))} domains active.")
    
    # Fetch Unread Emails from INBOX only
    # q='label:INBOX' ensures we process everything in Inbox, not just unread if you prefer
    try:
        results = service.users().messages().list(
            userId='me', 
            q='label:INBOX', 
            maxResults=config['batch_size']
        ).execute()
        messages = results.get('messages', [])
    except HttpError as e:
        print(f"❌ API Error fetching messages: {e}")
        return
    
    if not messages:
        print("✅ Inbox Zero. System Idle.")
        return

    print(f"📥 Processing {len(messages)} emails from Inbox...\n")
    
    # Batch Data Structures
    trash_ids = []
    move_map = {} # { "Label_ID": [msg_id1, msg_id2] }

    # --- SCAN LOOP ---
    for msg in messages:
        try:
            # Fetch partial data (metadata only) for speed
            txt = service.users().messages().get(userId='me', id=msg['id'], format='metadata').execute()
            headers = txt['payload']['headers']
            
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "(No Subject)")
            sender = next((h['value'] for h in headers if h['name'] == 'From'), "(Unknown)")
            snippet = txt.get('snippet', '')
            
            # Print Status
            print(f"📨 {subject[:40]:<40} | {sender[:25]:<25}", end=" ")
            
            # AI Decision
            decision = analyze_email_sovereign({'sender': sender, 'subject': subject, 'snippet': snippet}, config)
            
            if decision['action'] == "DELETE":
                print(f"-> 🗑️  DELETE ({decision.get('reason', 'Rule')})")
                trash_ids.append(msg['id'])
                
            elif decision['action'] == "LABEL":
                target_label = decision['label']
                # Resolve Label ID (Create if missing)
                label_id = label_manager.get_or_create(target_label)
                
                if label_id:
                    if label_id not in move_map:
                        move_map[label_id] = []
                    move_map[label_id].append(msg['id'])
                    print(f"-> 📂 {target_label}")
                else:
                    print("-> ⚠️ Label Error (Skip)")
            else:
                print("-> ⏭️  SKIP")
                
        except Exception as e:
            print(f"-> ❌ Error processing msg {msg['id']}: {e}")

    # --- EXECUTION LOOP ---
    print("\n⚡ EXECUTING BATCH OPERATIONS...")

    # 1. Execute Trash
    if trash_ids:
        print(f"🔥 Trashing {len(trash_ids)} items...")
        try:
            service.users().messages().batchModify(userId='me', body={
                "ids": trash_ids,
                "addLabelIds": ["TRASH"],
                "removeLabelIds": ["INBOX", "UNREAD", "IMPORTANT"] # Strip everything
            }).execute()
        except HttpError as e:
            print(f"   ❌ Trash Failed: {e}")

    # 2. Execute Moves
    for label_id, msg_ids in move_map.items():
        print(f"🚚 Moving {len(msg_ids)} items to [{label_id}]...")
        try:
            service.users().messages().batchModify(userId='me', body={
                "ids": msg_ids,
                "addLabelIds": [label_id],
                "removeLabelIds": ["INBOX"] # This ensures it leaves the Inbox
            }).execute()
        except HttpError as e:
            print(f"   ❌ Move Failed for {label_id}: {e}")

    print("\n✅ CYCLE COMPLETE.")

if __name__ == "__main__":
    main()
