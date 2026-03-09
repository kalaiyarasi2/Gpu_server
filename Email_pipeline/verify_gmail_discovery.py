import json
from auth import gmail

def verify():
    query = "is:unread has:attachment filename:pdf"
    print(f"--- [DIAGNOSTIC] Querying Gmail: {query} ---")
    
    try:
        resp = gmail.users().messages().list(
            userId="me",
            q=query,
            maxResults=10
        ).execute()
        
        msgs = resp.get("messages", [])
        print(f"Found {len(msgs)} messages matching query.")
        
        for m in msgs:
            msg_id = m['id']
            msg = gmail.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["Subject", "From", "Date"]
            ).execute()
            
            headers = {h['name']: h['value'] for h in msg['payload'].get('headers', [])}
            print(f"- ID: {msg_id}")
            print(f"  From:    {headers.get('From')}")
            print(f"  Subject: {headers.get('Subject')}")
            print(f"  Date:    {headers.get('Date')}")
            
            # Check parts
            full_msg = gmail.users().messages().get(userId="me", id=msg_id).execute()
            parts = full_msg.get('payload', {}).get('parts', [])
            print(f"  Parts count: {len(parts)}")
            for p in parts:
                print(f"    - Part: {p.get('filename') or '[No Filename]'} | {p.get('mimeType')}")
            print("-" * 30)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    verify()
