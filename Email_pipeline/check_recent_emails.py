import json
from auth import gmail

def check_recent():
    print("--- [DIAGNOSTIC] Checking 10 most recent messages ---")
    try:
        resp = gmail.users().messages().list(
            userId="me",
            maxResults=10
        ).execute()
        
        msgs = resp.get("messages", [])
        if not msgs:
            print("No messages found at all.")
            return

        for m in msgs:
            msg_id = m['id']
            msg = gmail.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["Subject", "From", "Date"]
            ).execute()
            
            labels = msg.get('labelIds', [])
            is_unread = "UNREAD" in labels
            
            headers = {h['name']: h['value'] for h in msg['payload'].get('headers', [])}
            print(f"- ID: {msg_id} | UNREAD: {is_unread}")
            print(f"  From:    {headers.get('From')}")
            print(f"  Subject: {headers.get('Subject')}")
            print(f"  Labels:  {labels}")
            
            # Check for PDF
            full_msg = gmail.users().messages().get(userId="me", id=msg_id).execute()
            parts = []
            payload = full_msg.get('payload', {})
            
            def find_filenames(p):
                 if 'parts' in p:
                     for sub in p['parts']:
                         find_filenames(sub)
                 else:
                     fname = p.get('filename')
                     if fname:
                         parts.append(fname)
            
            find_filenames(payload)
            print(f"  Attachments: {parts}")
            print("-" * 30)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_recent()
