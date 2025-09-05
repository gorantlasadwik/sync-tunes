#!/usr/bin/env python3
"""
Local HTTPS server for testing Sync Tunes with Spotify OAuth
This creates a temporary HTTPS tunnel for testing OAuth integrations
"""

import os
import sys
from pyngrok import ngrok
from app import app

def run_with_https():
    """Run the Flask app with HTTPS tunnel for OAuth testing"""
    
    # Set up environment variables for local testing
    os.environ['FLASK_ENV'] = 'development'
    os.environ['FLASK_DEBUG'] = 'True'
    
    # Start ngrok tunnel
    print("🚀 Starting HTTPS tunnel...")
    public_url = ngrok.connect(5000)
    https_url = public_url.replace('http://', 'https://')
    
    print(f"🌐 Your app is now available at: {https_url}")
    print(f"🔗 Spotify Redirect URI: {https_url}/spotify_callback")
    print(f"🔗 YouTube Redirect URI: {https_url}/youtube_callback")
    print("\n⚠️  IMPORTANT: Update your Spotify Dashboard with the new redirect URI!")
    print("   Go to: https://developer.spotify.com/dashboard")
    print(f"   Set Redirect URI to: {https_url}/spotify_callback")
    print("\n📱 Press Ctrl+C to stop the server")
    print("=" * 60)
    
    try:
        # Run the Flask app
        app.run(host='0.0.0.0', port=5000, debug=True)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down server...")
        ngrok.kill()
        sys.exit(0)

if __name__ == '__main__':
    run_with_https()
