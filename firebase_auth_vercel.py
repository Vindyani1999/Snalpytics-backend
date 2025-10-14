import firebase_admin
from firebase_admin import auth, credentials
import os
import json

def initialize_firebase():
    if not firebase_admin._apps:
        # Try to get credentials from environment variable first (for Vercel)
        firebase_key = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY')
        if firebase_key:
            try:
                # Parse the JSON string from environment variable
                service_account_info = json.loads(firebase_key)
                cred = credentials.Certificate(service_account_info)
                firebase_admin.initialize_app(cred)
            except Exception as e:
                print(f"Error initializing Firebase from env var: {e}")
                # Fallback to service account file for local development
                try:
                    cred = credentials.Certificate("serviceAccountKey.json")
                    firebase_admin.initialize_app(cred)
                except Exception as e:
                    print(f"Error initializing Firebase from file: {e}")
        else:
            # Use service account file for local development
            try:
                cred = credentials.Certificate("serviceAccountKey.json")
                firebase_admin.initialize_app(cred)
            except Exception as e:
                print(f"Error initializing Firebase: {e}")

# Initialize Firebase on import
initialize_firebase()

def verify_token(id_token):
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token['uid']
    except Exception as e:
        print(f"Token verification error: {e}")
        return None