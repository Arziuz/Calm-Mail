import flet as ft
import json
import threading
import os
import shutil
import time
import re
import ollama
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- CONFIG ---
CONFIG_FILE = 'config.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def load_config():
    defaults = {
        "model": "qwen3:8b",
        "batch_size": 50,
        "blacklist_domains": ["quora.com", "reddit.com", "temu.com"],
        "fixed_labels": ["Finance", "Work", "Personal", "Receipts", "Family"],
        "label_rules": {"Family": [], "Work": [], "Finance": []}
    }
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f: json.dump(defaults, f, indent=4)
        return defaults
    try:
        with open(CONFIG_FILE, 'r') as f:
            user = json.load(f)
            if "label_rules" not in user: user["label_rules"] = {}
            for k,v in defaults.items():
                if k not in user: user[k] = v
            return user
    except: return defaults

def save_config(config):
    with open(CONFIG_FILE, 'w') as f: json.dump(config, f, indent=4)

def main(page: ft.Page):
    page.title = "Calm Mail - Sovereign Agent"
    page.theme_mode = ft.ThemeMode.DARK
    page.window_width = 1500
    page.window_height = 950
    page.bgcolor = "#0f0f0f"
    page.padding = 0

    config = load_config()
    is_running = False

    # --- UI STATE ---
    log_column = ft.Column(spacing=2, scroll=ft.ScrollMode.ALWAYS, auto_scroll=True, expand=True)
    chat_column = ft.Column(spacing=10, scroll=ft.ScrollMode.ALWAYS, auto_scroll=True, expand=True)
    
    status_dot = ft.Container(width=12, height=12, border_radius=6, bgcolor="red")
    status_text = ft.Text("SYSTEM OFFLINE", color="red", weight="bold", size=12)

    def logger(msg, color="#cccccc"):
        ts = time.strftime('%H:%M:%S')
        log_column.controls.append(
            ft.Text(f"[{ts}] {msg}", color=color, font_family="Consolas", size=14, selectable=True)
        )
        page.update()

    # --- CHATBOT LOGIC ---
    def add_chat_message(role, text):
        align = ft.MainAxisAlignment.END if role == "user" else ft.MainAxisAlignment.START
        color = "#2a2a2a" if role == "user" else "#004400"
        chat_column.controls.append(
            ft.Row([
                ft.Container(
                    content=ft.Text(text, selectable=True),
                    bgcolor=color, padding=10, border_radius=8,
                    width=300 # Max width for bubble
                )
            ], alignment=align)
        )
        page.update()

    def process_chat_command(e):
        user_text = txt_chat_input.value
        if not user_text: return
        
        txt_chat_input.value = ""
        add_chat_message("user", user_text)
        page.update()

        threading.Thread(target=run_chat_ai, args=(user_text,), daemon=True).start()

    def run_chat_ai(user_text):
        try:
            prompt = f"""
            You are the Configuration Manager for Calm Mail.
            User Request: "{user_text}"
            
            Current Config:
            - Blacklist: {config['blacklist_domains']}
            - Labels: {config['fixed_labels']}
            
            Task: Decide how to modify the config based on user request.
            Supported Actions: BLACKLIST_ADD (domain), LABEL_CREATE (name), EXPLAIN.
            
            Output JSON ONLY:
            {{
                "action": "BLACKLIST_ADD" | "LABEL_CREATE" | "EXPLAIN",
                "target": "domain_or_label",
                "response": "Short confirmation message."
            }}
            """
            
            res = ollama.chat(model=config['model'], messages=[{'role':'user', 'content':prompt}])
            content = res['message']['content']
            start = content.find('{')
            end = content.rfind('}') + 1
            decision = json.loads(content[start:end])
            
            action = decision.get('action')
            target = decision.get('target')
            reply = decision.get('response', "Done.")

            updated = False
            if action == "BLACKLIST_ADD" and target:
                if target not in config['blacklist_domains']:
                    config['blacklist_domains'].append(target)
                    updated = True
            elif action == "LABEL_CREATE" and target:
                if target not in config['fixed_labels']:
                    config['fixed_labels'].append(target)
                    updated = True

            if updated:
                save_config(config)
                logger(f"⚙ Config Change: {action} -> {target}", "yellow")
            
            add_chat_message("ai", reply)

        except Exception as e:
            add_chat_message("ai", f"Error: {e}")

    # --- AGENT LOGIC ---
    def run_agent_logic():
        nonlocal is_running
        logger("--- INITIALIZING ---", "#00ff00")
        status_dot.bgcolor = "#00ff00"
        status_text.value = "ACTIVE"
        status_text.color = "#00ff00"
        page.update()

        try:
            if not os.path.exists('credentials.json'):
                logger("❌ MISSING CREDENTIALS", "red")
                stop_process()
                return
            
            creds = None
            if os.path.exists('token.json'): creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    creds = flow.run_local_server(port=0)
                with open('token.json', 'w') as t: t.write(creds.to_json())
            
            service = build('gmail', 'v1', credentials=creds)
            logger("✔ API Connected", "#00ff00")
            
            # Label Cache
            results = service.users().labels().list(userId='me').execute()
            label_cache = {l['name'].lower(): l['id'] for l in results.get('labels', [])}

            # CONTINUOUS LOOP
            while is_running:
                logger(f"Scanning Batch ({config['batch_size']})...", "cyan")
                
                # 1. Fetch Batch
                try:
                    msgs = service.users().messages().list(userId='me', q='label:INBOX', maxResults=config['batch_size']).execute().get('messages', [])
                except Exception as e: 
                    logger(f"API Error: {e}", "red")
                    break
                
                if not msgs:
                    logger("✨ Inbox Zero. Waiting 10s...", "#00ff00")
                    time.sleep(10)
                    continue
                
                trash_ids = []
                move_map = {}
                
                # 2. Process Batch
                for msg in msgs:
                    if not is_running: break
                    try:
                        txt = service.users().messages().get(userId='me', id=msg['id'], format='metadata').execute()
                        head = txt['payload']['headers']
                        sub = next((h['value'] for h in head if h['name']=='Subject'), "No Subject")
                        send = next((h['value'] for h in head if h['name']=='From'), "Unknown")
                        match = re.search(r'<(.+?)>', send)
                        email = match.group(1) if match else send
                        domain = email.split('@')[-1].lower().strip() if '@' in email else "unknown"

                        action, label = "SKIP", None
                        
                        # A. Blacklist
                        if any(b in domain for b in config['blacklist_domains']): 
                            action="DELETE"
                        
                        # B. Rules
                        if action=="SKIP":
                             for l, r in config['label_rules'].items():
                                 if any(x.lower() in email.lower() for x in r):
                                     action, label = "LABEL", l; break
                        
                        # C. AI
                        if action=="SKIP":
                             prompt_ai = f"Sender: {email}. Sub: {sub}. Labels: {json.dumps(config['fixed_labels'])}. Rules: Quora/Reddit/Social=DELETE. Output JSON {{'category': 'Label' or 'DELETE'}}."
                             try:
                                 res = ollama.chat(model=config['model'], messages=[{'role':'user', 'content':prompt_ai}])
                                 dec = json.loads(res['message']['content'][res['message']['content'].find('{'):res['message']['content'].rfind('}')+1])
                                 c = dec.get('category','INBOX')
                                 if c.upper() in ['DELETE','SPAM']: action="DELETE"
                                 elif c.upper()!="INBOX": action, label = "LABEL", c
                             except: pass
                        
                        # Queue Action
                        if action=="DELETE": 
                            logger(f"🗑 {domain}", "#ff4444")
                            trash_ids.append(msg['id'])
                        elif action=="LABEL": 
                            logger(f"📂 {domain} -> {label}", "#44aaff")
                            lid = label_cache.get(label.lower())
                            if not lid:
                                try: 
                                    l=service.users().labels().create(userId='me', body={"name":label,"labelListVisibility":"labelShow","messageListVisibility":"show"}).execute()
                                    lid=l['id']; label_cache[label.lower()]=lid
                                except: pass
                            if lid:
                                if lid not in move_map: move_map[lid]=[]
                                move_map[lid].append(msg['id'])
                    except: pass
                
                # 3. EXECUTE BATCH
                if not is_running: break
                
                if trash_ids:
                    logger(f"🔥 Incinerating {len(trash_ids)} items...", "#ff4444")
                    service.users().messages().batchModify(userId='me', body={"ids":trash_ids,"addLabelIds":["TRASH"],"removeLabelIds":["INBOX"]}).execute()

                for l, ids in move_map.items():
                    if l == "INBOX": continue
                    logger(f"🚚 Moving {len(ids)} items to {l}...", "#00ff00")
                    service.users().messages().batchModify(userId='me', body={"ids":ids,"addLabelIds":[l],"removeLabelIds":["INBOX"]}).execute()
                
                logger("Batch Done. Syncing...", "#ffffff")
                time.sleep(3) # Wait for Gmail to index changes

        except Exception as e: logger(f"Error: {e}", "red")
        stop_process()

    def stop_process():
        nonlocal is_running
        is_running = False
        btn_run.text = "START CALM MAIL"
        btn_run.icon = "play_arrow"
        btn_run.disabled = False
        btn_run.bgcolor = "#2a2a2a"
        status_dot.bgcolor = "red"
        status_text.value = "OFFLINE"
        status_text.color = "red"
        page.update()

    def start_click(e):
        nonlocal is_running
        if is_running:
            is_running = False
            btn_run.text = "STOPPING..."
            btn_run.disabled = True
        else:
            is_running = True
            btn_run.text = "STOP AGENT"
            btn_run.icon = "stop"
            btn_run.bgcolor = "#880000"
            threading.Thread(target=run_agent_logic, daemon=True).start()
        page.update()

    # --- SETTINGS ---
    def save_settings(e):
        config['model'] = txt_model.value
        config['blacklist_domains'] = [x.strip() for x in txt_black.value.split('\n') if x.strip()]
        new_labels = [x.strip() for x in txt_labels.value.split('\n') if x.strip()]
        config['fixed_labels'] = new_labels
        
        if dd_labels.value:
            config['label_rules'][dd_labels.value] = [x.strip() for x in txt_rules.value.split('\n') if x.strip()]
            
        # Cleanup Orphans
        keys_to_delete = [k for k in config['label_rules'] if k not in new_labels]
        for k in keys_to_delete: del config['label_rules'][k]

        save_config(config)
        update_dropdown()
        page.snack_bar = ft.SnackBar(ft.Text("Saved!"))
        page.snack_bar.open = True
        page.update()

    def on_dropdown_change(e):
        rules = config['label_rules'].get(dd_labels.value, [])
        txt_rules.value = "\n".join(rules)
        page.update()
        
    def update_dropdown():
        opts = []
        for l in config['fixed_labels']:
            opts.append(ft.dropdown.Option(l))
            if l not in config['label_rules']: config['label_rules'][l] = []
        dd_labels.options = opts
        if dd_labels.value not in config['fixed_labels']:
            dd_labels.value = config['fixed_labels'][0] if config['fixed_labels'] else None
        page.update()
    
    def on_rules_blur(e):
        if dd_labels.value:
             config['label_rules'][dd_labels.value] = [x.strip() for x in txt_rules.value.split('\n') if x.strip()]
             
    def on_file_pick(e: ft.FilePickerResultEvent):
        if e.files:
            shutil.copy(e.files[0].path, "credentials.json")
            if os.path.exists("token.json"): os.remove("token.json")
            cred_status.value = "✅ credentials.json found"; cred_status.color = "green"
            page.update()

    # --- LAYOUT ---
    file_picker = ft.FilePicker(on_result=on_file_pick)
    page.overlay.append(file_picker)
    
    # Dashboard
    txt_chat_input = ft.TextField(hint_text="Ask AI to block a domain...", expand=True, border_color="#333333", on_submit=process_chat_command)
    btn_send = ft.IconButton(icon="send", on_click=process_chat_command, icon_color="green")

    terminal_panel = ft.Container(
        content=log_column, bgcolor="black", border=ft.border.all(1, "#333333"), border_radius=8, padding=10, expand=2
    )
    chat_panel = ft.Container(
        content=ft.Column([
            ft.Text("AI COMMAND CENTER", weight="bold"),
            ft.Divider(color="#333333"),
            chat_column,
            ft.Row([txt_chat_input, btn_send])
        ], expand=True),
        bgcolor="#111111", border=ft.border.all(1, "#333333"), border_radius=8, padding=10, expand=1
    )
    btn_run = ft.ElevatedButton("START CALM MAIL", icon="play_arrow", on_click=start_click, height=50, bgcolor="#2a2a2a", color="white")
    
    dashboard = ft.Container(
        content=ft.Column([
            ft.Row([status_dot, status_text, ft.Container(expand=True), btn_run], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(height=10),
            ft.Row([terminal_panel, chat_panel], expand=True)
        ], expand=True), padding=20
    )

    # Settings
    cred_exists = os.path.exists("credentials.json")
    cred_status = ft.Text("✅ credentials.json found" if cred_exists else "❌ No credentials.json found", color="green" if cred_exists else "red")
    
    txt_model = ft.TextField(value=config['model'], label="Model ID")
    txt_black = ft.TextField(value="\n".join(config['blacklist_domains']), multiline=True, min_lines=4, label="Blacklist", expand=True)
    txt_labels = ft.TextField(value="\n".join(config['fixed_labels']), multiline=True, min_lines=4, label="Labels", expand=True)
    
    dd_labels = ft.Dropdown(
        label="Edit Rules For:",
        options=[ft.dropdown.Option(l) for l in config['fixed_labels']],
        value=config['fixed_labels'][0] if config['fixed_labels'] else None,
        on_change=on_dropdown_change, expand=True
    )
    initial_rules = config['label_rules'].get(dd_labels.value, []) if dd_labels.value else []
    txt_rules = ft.TextField(value="\n".join(initial_rules), multiline=True, min_lines=4, label="Rules (Emails)", expand=True, on_blur=on_rules_blur)

    settings = ft.Container(
        content=ft.Column([
            ft.Text("SECURITY", weight="bold"),
            ft.Row([cred_status, ft.ElevatedButton("Import Credentials", icon="upload_file", on_click=lambda _: file_picker.pick_files())], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Divider(),
            ft.Text("AI & RULES", weight="bold"),
            txt_model,
            ft.Row([ft.Column([txt_labels], expand=True), ft.Column([dd_labels, txt_rules], expand=True)], expand=True, spacing=20),
            txt_black,
            ft.ElevatedButton("SAVE SETTINGS", icon="save", on_click=save_settings)
        ], scroll=ft.ScrollMode.AUTO), padding=30
    )

    page.add(ft.Tabs(tabs=[ft.Tab(text="DASHBOARD", icon="dashboard", content=dashboard), ft.Tab(text="SETTINGS", icon="settings", content=settings)], expand=True))

if __name__ == "__main__":
    ft.app(target=main)
