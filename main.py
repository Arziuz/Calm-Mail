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

# --- CONFIG ---
CONFIG_FILE = 'config.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def load_config():
    defaults = {
        "model": "qwen3:8b",
        "batch_size": 20,
        "blacklist_domains": ["quora.com", "reddit.com", "temu.com"],
        "fixed_labels": ["Finance", "Work", "Personal", "Receipts", "Family"],
        "label_rules": {
            "Family": [],
            "Work": [],
            "Finance": []
        }
    }
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f: json.dump(defaults, f, indent=4)
        return defaults
    try:
        with open(CONFIG_FILE, 'r') as f:
            user = json.load(f)
            # Migration: Ensure label_rules exists
            if "label_rules" not in user: user["label_rules"] = {}
            # Migration: Move old 'family_emails' to label_rules['Family']
            if "family_emails" in user and user["family_emails"]:
                if "Family" not in user["label_rules"]: user["label_rules"]["Family"] = []
                user["label_rules"]["Family"].extend(user["family_emails"])
                del user["family_emails"]
            
            # Merge defaults
            for k,v in defaults.items():
                if k not in user: user[k] = v
            return user
    except: return defaults

def save_config(config):
    with open(CONFIG_FILE, 'w') as f: json.dump(config, f, indent=4)

def main(page: ft.Page):
    page.title = "Calm Mail - Sovereign Agent"
    page.theme_mode = ft.ThemeMode.DARK
    page.window_width = 1200
    page.window_height = 900
    page.bgcolor = "#0f0f0f"
    page.padding = 0

    config = load_config()
    is_running = False
    current_rule_label = config['fixed_labels'][0] if config['fixed_labels'] else "Family"

    # --- UI STATE ---
    log_column = ft.Column(spacing=0, scroll=ft.ScrollMode.ALWAYS, auto_scroll=True)
    status_dot = ft.Container(width=12, height=12, border_radius=6, bgcolor="red")
    status_text = ft.Text("SYSTEM OFFLINE", color="red", weight="bold", size=12)

    def logger(msg, color="#cccccc"):
        ts = time.strftime('%H:%M:%S')
        log_column.controls.append(
            ft.Container(
                content=ft.Text(f"[{ts}] {msg}", color=color, font_family="Consolas", size=14, selectable=True),
                padding=ft.padding.only(left=10, bottom=2)
            )
        )
        page.update()

    # --- FILE PICKER ---
    def on_dialog_result(e: ft.FilePickerResultEvent):
        if e.files:
            path = e.files[0].path
            shutil.copy(path, "credentials.json")
            if os.path.exists("token.json"): os.remove("token.json")
            page.snack_bar = ft.SnackBar(ft.Text("Credentials Imported! Restart Recommended."))
            page.snack_bar.open = True
            cred_status.value = "✅ credentials.json found"
            cred_status.color = "green"
            page.update()

    file_picker = ft.FilePicker(on_result=on_dialog_result)
    page.overlay.append(file_picker)

    # --- AGENT LOGIC ---
    def run_agent_logic():
        nonlocal is_running
        logger("--- INITIALIZING CALM MAIL ---", "#00ff00")
        status_dot.bgcolor = "#00ff00"
        status_text.value = "AGENT ACTIVE"
        status_text.color = "#00ff00"
        page.update()

        try:
            if not os.path.exists('credentials.json'):
                logger("❌ MISSING CREDENTIALS. Go to Settings.", "red")
                stop_process()
                return

            creds = None
            if os.path.exists('token.json'):
                creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            if not creds or not creds.valid:
                logger("⚠ Browser Auth Required...", "yellow")
                if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    creds = flow.run_local_server(port=0)
                with open('token.json', 'w') as t: t.write(creds.to_json())
            
            service = build('gmail', 'v1', credentials=creds)
            logger("✔ Authenticated", "#00ff00")

            # LABELS
            results = service.users().labels().list(userId='me').execute()
            label_cache = {l['name'].lower(): l['id'] for l in results.get('labels', [])}

            # SCAN
            logger(f"Scanning Inbox (Batch Size: {config['batch_size']})...", "cyan")
            msgs = service.users().messages().list(userId='me', q='label:INBOX', maxResults=config['batch_size']).execute().get('messages', [])

            if not msgs:
                logger("✔ Inbox Zero.", "#00ff00")
                stop_process()
                return

            trash_ids, move_map = [], {}

            for msg in msgs:
                if not is_running: break
                try:
                    txt = service.users().messages().get(userId='me', id=msg['id'], format='metadata').execute()
                    head = txt['payload']['headers']
                    sub = next((h['value'] for h in head if h['name']=='Subject'), "No Subject")
                    send = next((h['value'] for h in head if h['name']=='From'), "Unknown")
                    snip = txt.get('snippet', '')

                    match = re.search(r'<(.+?)>', send)
                    email = match.group(1) if match else send
                    domain = email.split('@')[-1].lower().strip() if '@' in email else "unknown"

                    action, label = "SKIP", None
                    
                    # 1. Blacklist
                    if any(b in domain for b in config['blacklist_domains']): 
                        action = "DELETE"
                    
                    # 2. DETERMINISTIC ROUTING (New Logic)
                    if action == "SKIP":
                        for lbl, rules in config['label_rules'].items():
                            # Check if sender matches any rule for this label
                            if any(rule.lower() in email.lower() for rule in rules if rule.strip()):
                                action, label = "LABEL", lbl
                                break
                    
                    # 3. AI Tribunal (Fallback)
                    if action == "SKIP":
                        prompt = f"Sender: {email} ({domain}). Sub: {sub}. Snip: {snip}. Labels: {json.dumps(config['fixed_labels'])}. Rules: Quora/Reddit/Social=DELETE. Finance=Bills. Output JSON {{'category': 'LabelName' or 'DELETE'}}."
                        try:
                            res = ollama.chat(model=config['model'], messages=[{'role':'user', 'content':prompt}])
                            dec = json.loads(res['message']['content'][res['message']['content'].find('{'):res['message']['content'].rfind('}')+1])
                            cat = dec.get('category', 'INBOX')
                            if cat.upper() in ["DELETE","SPAM","TRASH"]: action = "DELETE"
                            elif cat.upper() == "INBOX": action = "SKIP"
                            else: action, label = "LABEL", cat
                        except: pass

                    if action == "DELETE":
                        logger(f"🗑 [{domain}] {sub[:30]}...", "#ff4444")
                        trash_ids.append(msg['id'])
                    elif action == "LABEL":
                        logger(f"📂 [{domain}] {sub[:30]}... -> {label}", "#44aaff")
                        lid = label_cache.get(label.lower())
                        if not lid:
                            try:
                                l = service.users().labels().create(userId='me', body={"name":label,"labelListVisibility":"labelShow","messageListVisibility":"show"}).execute()
                                lid = l['id']
                                label_cache[label.lower()] = lid
                            except: pass
                        if lid:
                            if lid not in move_map: move_map[lid] = []
                            move_map[lid].append(msg['id'])
                    else:
                        logger(f"⏭ [{domain}] {sub[:30]}...", "#666666")
                except: pass

            if trash_ids:
                logger(f"🔥 Trashing {len(trash_ids)} items...", "#ff4444")
                service.users().messages().batchModify(userId='me', body={"ids":trash_ids,"addLabelIds":["TRASH"],"removeLabelIds":["INBOX","UNREAD"]}).execute()
            
            for label_id, msg_ids in move_map.items():
                if label_id == "INBOX": continue
                logger(f"🚚 Moving {len(msg_ids)} items to {label_id}...", "#00ff00")
                service.users().messages().batchModify(userId='me', body={"ids":msg_ids, "addLabelIds":[label_id], "removeLabelIds":["INBOX"]}).execute()
            
            logger("✔ CYCLE COMPLETE", "#ffffff")
        except Exception as e:
            logger(f"ERROR: {e}", "red")
        stop_process()

    def stop_process():
        nonlocal is_running
        is_running = False
        btn_run.text = "START CALM MAIL"
        btn_run.disabled = False
        btn_run.bgcolor = "#2a2a2a"
        status_dot.bgcolor = "red"
        status_text.value = "OFFLINE"
        status_text.color = "red"
        page.update()

    def start_click(e):
        nonlocal is_running
        if not is_running:
            is_running = True
            btn_run.text = "RUNNING..."
            btn_run.disabled = True
            btn_run.bgcolor = "#004400"
            page.update()
            threading.Thread(target=run_agent_logic, daemon=True).start()

    # --- SETTINGS HANDLERS ---
    def save_settings(e):
        # Save text fields
        config['model'] = txt_model.value
        config['blacklist_domains'] = [x.strip() for x in txt_black.value.split('\n') if x.strip()]
        config['fixed_labels'] = [x.strip() for x in txt_labels.value.split('\n') if x.strip()]
        
        # Save current rule text box to memory
        current_lbl = dd_labels.value
        if current_lbl:
            rules = [x.strip() for x in txt_rules.value.split('\n') if x.strip()]
            config['label_rules'][current_lbl] = rules
            
        save_config(config)
        
        # Update Dropdown Options in case labels changed
        update_dropdown()
        
        page.snack_bar = ft.SnackBar(ft.Text("Settings Saved!"))
        page.snack_bar.open = True
        page.update()

    def on_dropdown_change(e):
        # 1. Save previous text box content to config
        # (We can't easily know 'previous' without state, so we rely on Save button for commit usually)
        # But for UX, let's just load the new label's rules
        selected_label = dd_labels.value
        rules = config['label_rules'].get(selected_label, [])
        txt_rules.value = "\n".join(rules)
        page.update()
        
    def update_dropdown():
        # Refresh dropdown options from fixed_labels
        opts = []
        for l in config['fixed_labels']:
            opts.append(ft.dropdown.Option(l))
            # Ensure key exists in rules dict
            if l not in config['label_rules']: config['label_rules'][l] = []
            
        dd_labels.options = opts
        # Reset value if current selection deleted
        if dd_labels.value not in config['fixed_labels']:
            dd_labels.value = config['fixed_labels'][0] if config['fixed_labels'] else None
        page.update()
    
    def on_rules_blur(e):
        # Auto-save to memory when leaving text box
        if dd_labels.value:
             config['label_rules'][dd_labels.value] = [x.strip() for x in txt_rules.value.split('\n') if x.strip()]

    # --- LAYOUT COMPONENTS ---
    terminal_container = ft.Container(
        content=log_column, bgcolor="#000000", border=ft.border.all(1, "#333333"), border_radius=8, padding=15, expand=True
    )
    
    btn_run = ft.ElevatedButton("START CALM MAIL", icon="play_arrow", on_click=start_click, height=50, bgcolor="#2a2a2a", color="white")
    
    tab_dashboard = ft.Container(
        content=ft.Column([
            ft.Row([status_dot, status_text, ft.Container(expand=True), btn_run], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(height=10),
            terminal_container
        ], expand=True), padding=20
    )

    # Settings Components
    cred_exists = os.path.exists("credentials.json")
    cred_status = ft.Text("✅ credentials.json found" if cred_exists else "❌ No credentials.json found", color="green" if cred_exists else "red")
    btn_import = ft.ElevatedButton("Import 'credentials.json'", icon="upload_file", on_click=lambda _: file_picker.pick_files(allow_multiple=False))
    
    txt_model = ft.TextField(value=config['model'], label="Ollama Model ID", border_color="#444444")
    txt_black = ft.TextField(value="\n".join(config['blacklist_domains']), multiline=True, min_lines=4, label="Blacklist Domains", border_color="#444444", text_size=12, expand=True)
    txt_labels = ft.TextField(value="\n".join(config['fixed_labels']), multiline=True, min_lines=4, label="Labels (One per line)", border_color="#444444", text_size=12, expand=True)
    
    # Rules Engine Components
    dd_labels = ft.Dropdown(
        label="Select Label to Edit Rules",
        options=[ft.dropdown.Option(l) for l in config['fixed_labels']],
        value=config['fixed_labels'][0] if config['fixed_labels'] else None,
        on_change=on_dropdown_change,
        border_color="#444444",
        expand=True
    )
    
    # Init rules text box with first label's rules
    initial_rules = config['label_rules'].get(dd_labels.value, []) if dd_labels.value else []
    txt_rules = ft.TextField(
        value="\n".join(initial_rules), 
        multiline=True, 
        min_lines=4, 
        label="Emails/Domains for this Label (One per line)", 
        border_color="#444444", 
        text_size=12, 
        expand=True,
        on_blur=on_rules_blur # Save to memory on blur
    )

    tab_settings = ft.Container(
        content=ft.Column([
            ft.Text("SECURITY VAULT", weight="bold", size=16),
            ft.Container(content=ft.Row([cred_status, btn_import], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), bgcolor="#1a1a1a", padding=15, border_radius=8),
            ft.Divider(color="#333333"),
            
            ft.Text("AI CONFIGURATION", weight="bold", size=16),
            txt_model,
            ft.Divider(color="#333333"),
            
            ft.Text("ROUTING RULES", weight="bold", size=16),
            ft.Row([
                ft.Column([ft.Text("1. Define Labels"), txt_labels], expand=True),
                ft.Column([ft.Text("2. Define Rules"), dd_labels, txt_rules], expand=True)
            ], expand=True, spacing=20),
            
            ft.Container(height=10),
            ft.Text("GLOBAL BLACKLIST", weight="bold", size=16),
            txt_black,
            
            ft.Container(height=20),
            ft.Row([ft.ElevatedButton("SAVE ALL SETTINGS", icon="save", on_click=save_settings, height=50, width=200)], alignment=ft.MainAxisAlignment.END)
        ], scroll=ft.ScrollMode.AUTO), padding=30
    )

    t = ft.Tabs(
        selected_index=0, animation_duration=300,
        tabs=[ft.Tab(text="TERMINAL", icon="terminal", content=tab_dashboard), ft.Tab(text="SETTINGS", icon="security", content=tab_settings)],
        expand=True, divider_color="#333333"
    )

    page.add(t)

if __name__ == "__main__":
    ft.app(target=main)
