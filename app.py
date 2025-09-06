from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re
from datetime import datetime, timedelta
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import google.generativeai as genai
from thefuzz import fuzz
from groq import Groq

# Load environment variables
load_dotenv()

# Configure Gemini API
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_QUOTA_EXCEEDED = False  # Global flag to track quota status
GEMINI_QUOTA_RESET_TIME = None  # Track when quota was last reset

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    print(f"✅ Gemini API configured with key: {GEMINI_API_KEY[:10]}...")

# Configure Groq API
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
if GROQ_API_KEY:
    print(f"✅ Groq API configured with key: {GROQ_API_KEY[:10]}...")
else:
    print("⚠️ Groq API key not found - will use fallback parsing only")

def check_and_reset_gemini_quota():
    """Check if 24 hours have passed since last quota reset and reset if needed"""
    global GEMINI_QUOTA_EXCEEDED, GEMINI_QUOTA_RESET_TIME
    
    current_time = datetime.now()
    
    # If quota is exceeded and we haven't reset in 24 hours, reset it
    if GEMINI_QUOTA_EXCEEDED and GEMINI_QUOTA_RESET_TIME:
        time_since_reset = current_time - GEMINI_QUOTA_RESET_TIME
        if time_since_reset.total_seconds() >= 24 * 60 * 60:  # 24 hours
            GEMINI_QUOTA_EXCEEDED = False
            GEMINI_QUOTA_RESET_TIME = current_time
            print("🔄 Gemini quota automatically reset after 24 hours")
    
    # If quota is exceeded but we haven't set a reset time, set it now
    elif GEMINI_QUOTA_EXCEEDED and not GEMINI_QUOTA_RESET_TIME:
        GEMINI_QUOTA_RESET_TIME = current_time
        print("⏰ Gemini quota exceeded - will auto-reset in 24 hours")

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
# Database Configuration
if os.getenv('DATABASE_URL'):
    # Production - PostgreSQL on Railway
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_timeout': 30,
        'pool_recycle': 300,
        'pool_pre_ping': True,
        'pool_size': 10,
        'max_overflow': 20
    }
else:
    # Development - SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sync_tunes.db?timeout=30'
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_timeout': 30,
        'pool_recycle': -1,
        'pool_pre_ping': True,
        'connect_args': {
            'timeout': 30,
            'check_same_thread': False
        }
    }

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# OAuth Configuration
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

# Dynamic redirect URI based on environment
if os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RENDER') or os.getenv('FLASK_ENV') == 'production':
    # Production - use HTTPS
    if os.getenv('RAILWAY_ENVIRONMENT'):
        base_url = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'https://your-app.railway.app')
    elif os.getenv('RENDER'):
        base_url = os.getenv('RENDER_EXTERNAL_URL', 'https://sync-tunes.onrender.com')
    else:
        # Fallback for production
        base_url = 'https://sync-tunes.onrender.com'
    SPOTIFY_REDIRECT_URI = f"{base_url}/spotify_callback"
    YOUTUBE_REDIRECT_URI = f"{base_url}/youtube_callback"
else:
    # Development - use HTTP
    SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI', 'http://localhost:5000/spotify_callback')
    YOUTUBE_REDIRECT_URI = os.getenv('YOUTUBE_REDIRECT_URI', 'http://localhost:5000/youtube_callback')

# YouTube OAuth Configuration
YOUTUBE_CLIENT_ID = os.getenv('YOUTUBE_CLIENT_ID')
YOUTUBE_CLIENT_SECRET = os.getenv('YOUTUBE_CLIENT_SECRET')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Custom Jinja2 filters
@app.template_filter('is_admin')
def is_admin(user):
    """Check if user is an admin"""
    return hasattr(user, 'admin_id')

@app.template_filter('is_user')
def is_user(user):
    """Check if user is a regular user"""
    return hasattr(user, 'user_id')

def fetch_spotify_playlists(user_id, access_token):
    """Fetch user's Spotify playlists and store them"""
    try:
        # Validate token first
        if not access_token:
            print("No Spotify access token provided")
            return False
            
        # Create Spotify client with error handling
        sp = spotipy.Spotify(auth=access_token)
        
        # Test the token first with better error handling
        try:
            user_info = sp.current_user()
            print(f"Spotify user info: {user_info}")
        except Exception as token_error:
            print(f"Spotify token error: {token_error}")
            # Check if it's a 403 error specifically
            if hasattr(token_error, 'http_status') and token_error.http_status == 403:
                print("Spotify 403 error - user may not be registered or app not configured properly")
                flash('Spotify connection failed: Please check if your Spotify account is properly set up and try reconnecting.', 'error')
            else:
                flash('Spotify connection failed: Invalid or expired token. Please reconnect.', 'error')
            
            # Mark account as disconnected
            platform = Platform.query.filter_by(platform_name='Spotify').first()
            if platform:
                account = UserPlatformAccount.query.filter_by(
                    user_id=user_id,
                    platform_id=platform.platform_id
                ).first()
                if account:
                    account.auth_token = None
                    db.session.commit()
            return False
        
        playlists = sp.current_user_playlists()
        
        # Get user's platform account
        platform = Platform.query.filter_by(platform_name='Spotify').first()
        user_account = UserPlatformAccount.query.filter_by(
            user_id=user_id,
            platform_id=platform.platform_id
        ).first()
        
        if not user_account:
            return
        
        # Clear existing playlists for this account
        existing_playlist_ids = [p.playlist_id for p in Playlist.query.filter_by(account_id=user_account.account_id).all()]
        
        # Clear PlaylistSong relationships first to avoid foreign key issues
        if existing_playlist_ids:
            PlaylistSong.query.filter(PlaylistSong.playlist_id.in_(existing_playlist_ids)).delete(synchronize_session=False)
        
        # Now delete the playlists
        Playlist.query.filter_by(account_id=user_account.account_id).delete()
        
        # Add new playlists
        for playlist_data in playlists['items']:
            playlist = Playlist(
                account_id=user_account.account_id,
                name=playlist_data['name'],
                description=playlist_data.get('description', ''),
                last_updated=datetime.now().date(),
                platform_playlist_id=playlist_data['id']
            )
            db.session.add(playlist)
            
            # Get playlist tracks
            tracks = sp.playlist_tracks(playlist_data['id'])
            for track_data in tracks['items']:
                track = track_data['track']
                if track:
                    # Create or get song
                    song = Song.query.filter_by(
                        title=track['name'],
                        artist=track['artists'][0]['name'] if track['artists'] else 'Unknown Artist'
                    ).first()
                    
                    if not song:
                        song = Song(
                            title=track['name'],
                            artist=track['artists'][0]['name'] if track['artists'] else 'Unknown Artist',
                            album=track['album']['name'] if track['album'] else 'Unknown Album',
                            duration=track['duration_ms'] // 1000
                        )
                        db.session.add(song)
                        db.session.flush()
                    
                    # Check if platform song mapping already exists
                    existing_platform_song = PlatformSong.query.filter_by(
                        song_id=song.song_id,
                        platform_id=platform.platform_id
                    ).first()
                    
                    if not existing_platform_song:
                        platform_song = PlatformSong(
                            song_id=song.song_id,
                            platform_id=platform.platform_id,
                            platform_specific_id=track['id']
                        )
                        db.session.add(platform_song)
                    
                    # Check if playlist song relationship already exists
                    existing_playlist_song = PlaylistSong.query.filter_by(
                        playlist_id=playlist.playlist_id,
                        song_id=song.song_id
                    ).first()
                    
                    if not existing_playlist_song:
                        playlist_song = PlaylistSong(
                            playlist_id=playlist.playlist_id,
                            song_id=song.song_id,
                            added_at=datetime.now().date()
                        )
                        db.session.add(playlist_song)
        
        db.session.commit()
        
    except Exception as e:
        print(f"Error fetching Spotify playlists: {e}")
        db.session.rollback()

def fetch_youtube_playlists(user_id, access_token):
    """Fetch user's YouTube playlists with pagination"""
    try:
        # Validate token first
        if not access_token:
            print("No YouTube access token provided")
            return False
            
        # Get user's platform account
        platform = Platform.query.filter_by(platform_name='YouTube').first()
        user_account = UserPlatformAccount.query.filter_by(
            user_id=user_id,
            platform_id=platform.platform_id
        ).first()
        
        if not user_account:
            return False
        
        # Clear existing playlists for this account with better transaction handling
        existing_playlist_ids = [p.playlist_id for p in Playlist.query.filter_by(account_id=user_account.account_id).all()]
        
        if existing_playlist_ids:
            PlaylistSong.query.filter(PlaylistSong.playlist_id.in_(existing_playlist_ids)).delete(synchronize_session=False)
            db.session.flush()  # Flush the deletes
        
        Playlist.query.filter_by(account_id=user_account.account_id).delete()
        db.session.flush()  # Flush the playlist deletes
        
        # Use the access token to call YouTube Data API v3
        import requests
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        
        # Get user's playlists
        playlists_url = "https://www.googleapis.com/youtube/v3/playlists"
        params = {
            'part': 'snippet,contentDetails',
            'mine': 'true',
            'maxResults': 50
        }
        
        response = requests.get(playlists_url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            playlists = data.get('items', [])
        elif response.status_code == 401:
            # Token expired or invalid
            print("YouTube token expired or invalid")
            user_account.auth_token = None
            db.session.commit()
            return False
        else:
            print(f"YouTube API error: {response.status_code} - {response.text}")
            return False
        
        # Process playlists
        for playlist_data in playlists:
                snippet = playlist_data['snippet']
                playlist_id = playlist_data['id']
                playlist = Playlist(
                    account_id=user_account.account_id,
                    name=snippet.get('title', 'Unknown Playlist'),
                    description=snippet.get('description', ''),
                    last_updated=datetime.now().date(),
                    platform_playlist_id=playlist_id
                )
                db.session.add(playlist)
                db.session.flush()
                
                # Get playlist items with pagination
                items_url = "https://www.googleapis.com/youtube/v3/playlistItems"
                next_page_token = None
                
                while True:
                    items_params = {
                        'part': 'snippet,contentDetails',
                        'playlistId': playlist_id,
                        'maxResults': 50
                    }
                    
                    if next_page_token:
                        items_params['pageToken'] = next_page_token
                    
                    items_response = requests.get(items_url, headers=headers, params=items_params)
                    
                    if items_response.status_code == 200:
                        items_data = items_response.json()
                        items = items_data.get('items', [])
                        
                        for item in items:
                            snippet = item['snippet']
                            video_id = snippet['resourceId']['videoId']
                            
                            # Parse YouTube title using fallback parser for bulk operations
                            raw_title = snippet.get('title', 'Unknown Title')
                            channel_title = snippet.get('videoOwnerChannelTitle', 'Unknown Artist')
                            
                            # Use fallback parser for bulk playlist fetching to avoid API limits
                            parsed_song_name, parsed_artist = parse_youtube_title_fallback(raw_title, channel_title)
                            
                            # Log the parsing for debugging
                            print(f"YouTube title parsing (bulk): '{raw_title}' -> Song: '{parsed_song_name}', Artist: '{parsed_artist}'")
                            
                            # Store the original YouTube title in the song's album field for later Gemini parsing
                            # This way we can access it during sync without changing the database schema
                            
                            # Create or get song
                            song = Song.query.filter_by(
                                title=parsed_song_name,
                                artist=parsed_artist
                            ).first()
                            
                            if not song:
                                song = Song(
                                    title=parsed_song_name,
                                    artist=parsed_artist,
                                    album=f"YouTube_ORIGINAL:{raw_title}",  # Store original title for Gemini parsing
                                    duration=0
                                )
                                db.session.add(song)
                                db.session.flush()
                            
                            # Check if platform song mapping already exists
                            existing_platform_song = PlatformSong.query.filter_by(
                                song_id=song.song_id,
                                platform_id=platform.platform_id
                            ).first()
                            
                            if not existing_platform_song:
                                platform_song = PlatformSong(
                                    song_id=song.song_id,
                                    platform_id=platform.platform_id,
                                    platform_specific_id=video_id
                                )
                                db.session.add(platform_song)
                            
                            # Check if playlist song relationship already exists
                            existing_playlist_song = PlaylistSong.query.filter_by(
                                playlist_id=playlist.playlist_id,
                                song_id=song.song_id
                            ).first()
                            
                            if not existing_playlist_song:
                                playlist_song = PlaylistSong(
                                    playlist_id=playlist.playlist_id,
                                    song_id=song.song_id,
                                    added_at=datetime.now().date()
                                )
                                db.session.add(playlist_song)
                        
                        # Check if there are more pages
                        next_page_token = items_data.get('nextPageToken')
                        if not next_page_token:
                            break
                    else:
                        break
        
        db.session.commit()
        
    except Exception as e:
        print(f"Error fetching YouTube playlists: {e}")
        db.session.rollback()
        raise  # Re-raise the exception so calling function can handle it

def create_spotify_playlist_api(access_token, name, description):
    """Create a new Spotify playlist"""
    try:
        sp = spotipy.Spotify(auth=access_token)
        user_info = sp.current_user()
        user_id = user_info['id']
        
        playlist = sp.user_playlist_create(
            user_id, 
            name, 
            public=False, 
            description=description
        )
        
        return playlist
        
    except Exception as e:
        print(f"Error creating Spotify playlist: {e}")
        return None

def reset_gemini_quota():
    """Reset the Gemini quota flag when a new API key is provided"""
    global GEMINI_QUOTA_EXCEEDED
    GEMINI_QUOTA_EXCEEDED = False
    print("🔄 Gemini quota flag reset - ready to use new API key")

def parse_youtube_title_with_groq(title, channel_title=None):
    """Parse YouTube video title using Groq AI for intelligent extraction"""
    if not title:
        return "Unknown Title", "Unknown Artist"
    
    if not GROQ_API_KEY:
        print("Groq API key not available, using fallback parsing")
        return parse_youtube_title_fallback(title, channel_title)
    
    try:
        # Initialize Groq client with clean configuration
        print(f"Initializing Groq client with API key: {GROQ_API_KEY[:10]}...")
        client = Groq(api_key=GROQ_API_KEY)
        print("Groq client initialized successfully")
        
        # Enhanced prompt for Groq
        prompt = f"""
You are a music industry expert. I need you to extract the clean song name and artist from this YouTube video title.

YOUTUBE TITLE: "{title}"
CHANNEL: "{channel_title or 'Unknown'}"

TASK: Extract the clean song name and artist name.

IMPORTANT RULES:
- Extract ONLY the song name (not album/movie names)
- Remove "Official Video", "Lyrics", "4K", "HD", "Full Song", "Video Songs", "Full Video Songs", etc.
- For titles with ":" or "||", the part before is usually the song name
- For titles with "by" or "from", extract the part before these words
- For titles with " - ", the part AFTER the dash is often the song name
- Look for patterns like "Movie Name - Song Name" and extract the song name
- PRESERVE the original song name if it's already clean
- Don't change song names (e.g., "Telisiney Na Nuvvey" stays "Telisiney Na Nuvvey")

EXAMPLES:
- "UNPLUGGED Full Audio Song – Jeena Jeena by Sachin - Jigar" → Song: "Jeena Jeena", Artist: "Sachin - Jigar"
- "Baarish Ki Jaaye | B Praak Ft Nawazuddin Siddiqui & Sunanda Sharma" → Song: "Baarish Ki Jaaye", Artist: "B Praak"
- "Ae Dil Hai Mushkil Title Track Full Video" → Song: "Ae Dil Hai Mushkil", Artist: "Unknown Artist"
- "Saripodhaa Sanivaaram - Bhaga Bhaga Lyrical | Nani | Priyanka Mohan" → Song: "Bhaga Bhaga", Artist: "Unknown Artist"
- "Kaifi Khalil - Kahani Suno 2.0 [Official Music Video]" → Song: "Kahani Suno 2.0", Artist: "Kaifi Khalil"
- "Darshana Full Video Song | Vinaro Bhagyamu Vishnu Katha | Kiran Abbavaram | Chaitan Bharadwaj" → Song: "Darshana", Artist: "Kiran Abbavaram"

Respond in this EXACT JSON format:
{{
    "song_name": "Clean Song Name",
    "artist_name": "Artist Name or Unknown Artist"
}}
"""

        # Get response from Groq
        try:
            response = client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model="llama-3.3-70b-versatile",  # Using Llama 3.3 70B for best results
                temperature=0.1,  # Low temperature for consistent parsing
                max_tokens=200
            )
        except Exception as model_error:
            print(f"Llama 3.3 70B model failed, trying Mixtral fallback: {model_error}")
            # Fallback to Mixtral if Llama 3.3 70B is not available
            response = client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model="mixtral-8x7b-32768",  # Fallback to Mixtral
                temperature=0.1,
                max_tokens=200
            )
        
        # Parse the response
        try:
            response_text = response.choices[0].message.content.strip()
            
            # Clean the response text - remove markdown code blocks if present
            if response_text.startswith('```'):
                response_text = response_text.replace('```', '').strip()
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').strip()
            
            # Parse JSON response
            result = json.loads(response_text)
            song_name = result.get('song_name', title).strip()
            artist_name = result.get('artist_name', 'Unknown Artist').strip()
            
            # Validate the result
            if not song_name or song_name == "Unknown Title":
                song_name = title
            
            print(f"Groq parsing: '{title}' -> Song: '{song_name}', Artist: '{artist_name}'")
            return song_name, artist_name
            
        except json.JSONDecodeError as e:
            print(f"Groq returned invalid JSON: {response_text}")
            print(f"JSON parsing error: {e}")
            return parse_youtube_title_fallback(title, channel_title)
            
    except Exception as e:
        print(f"Groq API error: {e}")
        return parse_youtube_title_fallback(title, channel_title)

def get_spotify_song_name_from_youtube_url_groq(video_id, original_title, channel_title=None):
    """Use Groq with YouTube video URL to get the exact Spotify song name"""
    if not GROQ_API_KEY:
        return None, None, None, 0.0
    
    try:
        # Initialize Groq client with clean configuration
        print(f"Initializing Groq client with API key: {GROQ_API_KEY[:10]}...")
        client = Groq(api_key=GROQ_API_KEY)
        print("Groq client initialized successfully")
        
        # Create YouTube video URL
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Enhanced Groq prompt with YouTube URL
        prompt = f"""
You are a music industry expert. I need you to find the EXACT song name that exists on Spotify for this YouTube video.

YOUTUBE VIDEO INFORMATION:
- Video URL: {youtube_url}
- Original Title: "{original_title}"
- Channel: "{channel_title or 'Unknown'}"

TASK:
1. Analyze the YouTube video URL and title
2. Extract the EXACT song name that would be found on Spotify
3. Provide the correct artist name
4. Provide the correct album/movie name
5. Give a confidence score (0.0 to 1.0)

IMPORTANT RULES:
- The song name must be EXACTLY as it appears on Spotify
- For Bollywood/Tollywood songs, use the official song name
- For international songs, use the standard English title
- Remove any "Full Video", "Lyrical", "Official", "HD" etc. from the title
- For songs like "Movie Name - Song Name", extract ONLY the song name
- Be very precise - this will be used to search Spotify directly

EXAMPLES:
- "Gilehriyaan - Lyrical Video | Dangal | Aamir Khan" → Song: "Gilehriyaan", Artist: "Shreya Ghoshal", Album: "Dangal"
- "Aagi Aagi Full Video Song | Ee Nagaraniki Emaindi" → Song: "Aagi Aagi", Artist: "Sid Sriram", Album: "Ee Nagaraniki Emaindi"
- "Samjhawan Unplugged Full Video" → Song: "Samjhawan", Artist: "Arijit Singh", Album: "Humpty Sharma Ki Dulhania"

Respond in this EXACT JSON format:
{{
    "song_name": "Exact Song Name for Spotify",
    "artist_name": "Correct Artist Name",
    "album_name": "Album or Movie Name",
    "confidence": 0.95
}}
"""

        # Get response from Groq
        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1,
            max_tokens=300
        )
        
        # Parse the response
        try:
            response_text = response.choices[0].message.content.strip()
            
            # Clean the response text
            if response_text.startswith('```'):
                response_text = response_text.replace('```', '').strip()
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').strip()
            
            # Parse JSON response
            result = json.loads(response_text)
            song_name = result.get('song_name', '').strip()
            artist_name = result.get('artist_name', '').strip()
            album_name = result.get('album_name', '').strip()
            confidence = float(result.get('confidence', 0.0))
            
            # Validate the result
            if not song_name:
                return None, None, None, 0.0
            
            print(f"Groq URL analysis: '{original_title}' -> Song: '{song_name}', Artist: '{artist_name}', Album: '{album_name}', Confidence: {confidence:.2f}")
            return song_name, artist_name, album_name, confidence
            
        except json.JSONDecodeError as e:
            print(f"Groq returned invalid JSON: {response_text}")
            print(f"JSON parsing error: {e}")
            return None, None, None, 0.0
            
    except Exception as e:
        print(f"Groq API error for URL analysis: {e}")
        return None, None, None, 0.0

def analyze_youtube_description_groq(video_id, original_title, channel_title=None):
    """Analyze YouTube video description using Groq to extract correct song information"""
    if not GROQ_API_KEY:
        return None, None, None, 0.0
    
    try:
        # Initialize Groq client with clean configuration
        print(f"Initializing Groq client with API key: {GROQ_API_KEY[:10]}...")
        client = Groq(api_key=GROQ_API_KEY)
        print("Groq client initialized successfully")
        
        # Fetch video description using YouTube API
        if not YOUTUBE_API_KEY:
            return None, None, None, 0.0
        
        import requests
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'snippet',
            'id': video_id,
            'key': YOUTUBE_API_KEY
        }
        
        response = requests.get(url, params=params)
        if response.status_code != 200:
            return None, None, None, 0.0
        
        data = response.json()
        if not data.get('items'):
            return None, None, None, 0.0
        
        description = data['items'][0]['snippet'].get('description', '')
        
        if not description:
            return None, None, None, 0.0
        
        # Enhanced Groq prompt for description analysis
        prompt = f"""
You are a music industry expert. I need you to extract the correct song information from this YouTube video description.

YOUTUBE VIDEO INFORMATION:
- Original Title: "{original_title}"
- Channel: "{channel_title or 'Unknown'}"
- Description: "{description[:1000]}..."  # Truncated for context

TASK:
1. Analyze the YouTube video description
2. Extract the EXACT song name that would be found on Spotify
3. Provide the correct artist name
4. Provide the correct album/movie name
5. Give a confidence score (0.0 to 1.0)

IMPORTANT RULES:
- The song name must be EXACTLY as it appears on Spotify
- Look for official song names in the description
- Extract artist names from credits or performer information
- Extract album/movie names from production credits
- Be very precise - this will be used to search Spotify directly

Respond in this EXACT JSON format:
{{
    "song_name": "Exact Song Name for Spotify",
    "artist_name": "Correct Artist Name",
    "album_name": "Album or Movie Name",
    "confidence": 0.95
}}
"""

        # Get response from Groq
        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1,
            max_tokens=300
        )
        
        # Parse the response
        try:
            response_text = response.choices[0].message.content.strip()
            
            # Clean the response text
            if response_text.startswith('```'):
                response_text = response_text.replace('```', '').strip()
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').strip()
            
            # Parse JSON response
            result = json.loads(response_text)
            song_name = result.get('song_name', '').strip()
            artist_name = result.get('artist_name', '').strip()
            album_name = result.get('album_name', '').strip()
            confidence = float(result.get('confidence', 0.0))
            
            # Validate the result
            if not song_name:
                return None, None, None, 0.0
            
            print(f"Groq description analysis: '{original_title}' -> Song: '{song_name}', Artist: '{artist_name}', Album: '{album_name}', Confidence: {confidence:.2f}")
            return song_name, artist_name, album_name, confidence
            
        except json.JSONDecodeError as e:
            print(f"Groq returned invalid JSON: {response_text}")
            print(f"JSON parsing error: {e}")
            return None, None, None, 0.0
            
    except Exception as e:
        print(f"Groq API error for description analysis: {e}")
        return None, None, None, 0.0


def parse_youtube_title_with_gemini(title, channel_title=None):
    """Parse YouTube video title using Gemini AI for intelligent extraction (for selected songs only)"""
    if not title:
        return "Unknown Title", "Unknown Artist"
    
    # If Gemini API is not available or quota exceeded, fallback to regex parser
    if not GEMINI_API_KEY or GEMINI_QUOTA_EXCEEDED:
        return parse_youtube_title_fallback(title, channel_title)
    
    try:
        # Create Gemini model
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Create a focused prompt for Gemini to extract song name only
        prompt = f"""
You are a music industry expert. I need you to extract ONLY the clean song name from this YouTube video title.

YouTube Title: "{title}"
Channel Name: "{channel_title or 'Unknown'}"

TASK: Extract the clean song name from the YouTube title.

IMPORTANT RULES:
- Extract ONLY the song name (not album/movie names)
- Remove "Official Video", "Lyrics", "4K", "HD", "Full Song", "Video Songs", "Full Video Songs", etc.
- For titles with ":" or "||", the part before is usually the song name
- For titles with "by" or "from", extract the part before these words
- For titles with " - ", the part AFTER the dash is often the song name
- Look for patterns like "Movie Name - Song Name" and extract the song name
- PRESERVE the original song name if it's already clean
- Don't change song names (e.g., "Telisiney Na Nuvvey" stays "Telisiney Na Nuvvey")

EXAMPLES:
- "UNPLUGGED Full Audio Song – Jeena Jeena by Sachin - Jigar" → "Jeena Jeena"
- "Baarish Ki Jaaye | B Praak Ft Nawazuddin Siddiqui & Sunanda Sharma" → "Baarish Ki Jaaye"
- "Ae Dil Hai Mushkil Title Track Full Video" → "Ae Dil Hai Mushkil"
- "Saripodhaa Sanivaaram - Bhaga Bhaga Lyrical | Nani | Priyanka Mohan" → "Bhaga Bhaga"
- "Movie Name - Song Name | Artist" → "Song Name"
- "Milne Hai Mujhse Aayi Aashiqui 2 Full Video Song" → "Milne Hai Mujhse Aayi"
- "The PropheC - To The Stars | Official Video" → "To The Stars"

Respond with ONLY the clean song name, nothing else.
"""

        # Get response from Gemini
        try:
            response = model.generate_content(prompt)
        except Exception as e:
            if "quota" in str(e).lower() or "429" in str(e):
                print(f"Gemini API quota exceeded for title parsing: {e}")
                # Fallback to simple title cleaning
                return parse_youtube_title_fallback(title, channel_title)
            else:
                print(f"Gemini API error for title parsing: {e}")
                # Fallback to simple title cleaning
                return parse_youtube_title_fallback(title, channel_title)
        
        # Parse the response (just song name)
        try:
            # Clean the response text - remove markdown code blocks if present
            response_text = response.text.strip()
            if response_text.startswith('```'):
                response_text = response_text.replace('```', '').strip()
            
            song_name = response_text.strip()
            
            # Validate the result
            if not song_name or song_name == "Unknown Title":
                song_name = title
            
            print(f"Gemini parsing (selected): '{title}' -> Song: '{song_name}'")
            return song_name, None  # Return None for artist since we'll search for it separately
            
        except json.JSONDecodeError as e:
            print(f"Gemini returned invalid JSON: {response.text}")
            print(f"JSON parsing error: {e}")
            return parse_youtube_title_fallback(title, channel_title)
            
    except Exception as e:
        print(f"Gemini API error: {e}")
        return parse_youtube_title_fallback(title, channel_title)

def parse_youtube_title_for_sync(title, channel_title=None):
    """Parse YouTube video title using Gemini AI for selected songs during sync, with Groq fallback"""
    # Check and reset Gemini quota if 24 hours have passed
    check_and_reset_gemini_quota()
    
    # Try Gemini first
    if GEMINI_API_KEY and not GEMINI_QUOTA_EXCEEDED:
        try:
            return parse_youtube_title_with_gemini(title, channel_title)
        except Exception as e:
            print(f"Gemini parsing failed: {e}")
    
    # Fallback to Groq if Gemini fails or quota exceeded
    if GROQ_API_KEY:
        print("Using Groq as fallback for title parsing...")
        return parse_youtube_title_with_groq(title, channel_title)
    
    # Final fallback to regex parsing
    print("Using regex fallback for title parsing...")
    return parse_youtube_title_fallback(title, channel_title)

def get_spotify_song_name_from_youtube_url(video_id, original_title, channel_title=None):
    """Use Gemini with YouTube video URL to get the exact Spotify song name, with Groq fallback"""
    global GEMINI_QUOTA_EXCEEDED
    
    # Check and reset Gemini quota if 24 hours have passed
    check_and_reset_gemini_quota()
    
    # Try Gemini first
    if GEMINI_API_KEY and not GEMINI_QUOTA_EXCEEDED:
        try:
            return get_spotify_song_name_from_youtube_url_gemini(video_id, original_title, channel_title)
        except Exception as e:
            print(f"Gemini URL analysis failed: {e}")
    
    # Fallback to Groq if Gemini fails or quota exceeded
    if GROQ_API_KEY:
        print("Using Groq as fallback for URL analysis...")
        return get_spotify_song_name_from_youtube_url_groq(video_id, original_title, channel_title)
    
    return None, None, None, 0.0

def get_spotify_song_name_from_youtube_url_gemini(video_id, original_title, channel_title=None):
    """Use Gemini with YouTube video URL to get the exact Spotify song name"""
    global GEMINI_QUOTA_EXCEEDED
    
    if not GEMINI_API_KEY or GEMINI_QUOTA_EXCEEDED:
        return None, None, None, 0.0
    
    try:
        # Create Gemini model
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Create YouTube video URL
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Enhanced Gemini prompt with YouTube URL
        prompt = f"""
You are a music industry expert. I need you to find the EXACT song name that exists on Spotify for this YouTube video.

YOUTUBE VIDEO INFORMATION:
- Video URL: {youtube_url}
- Original Title: "{original_title}"
- Channel: "{channel_title or 'Unknown'}"

TASK:
1. Analyze the YouTube video URL and title
2. Extract the EXACT song name that would be found on Spotify
3. Provide the correct artist name
4. Provide the correct album/movie name
5. Give a confidence score (0.0 to 1.0)

IMPORTANT RULES:
- The song name must be EXACTLY as it appears on Spotify
- For Bollywood/Tollywood songs, use the official song name
- For international songs, use the standard English title
- Remove any "Full Video", "Lyrical", "Official", "HD" etc. from the title
- For songs like "Movie Name - Song Name", extract ONLY the song name
- Be very precise - this will be used to search Spotify directly

EXAMPLES:
- "Gilehriyaan - Lyrical Video | Dangal | Aamir Khan" → Song: "Gilehriyaan", Artist: "Shreya Ghoshal", Album: "Dangal"
- "Aagi Aagi Full Video Song | Ee Nagaraniki Emaindi" → Song: "Aagi Aagi", Artist: "Sid Sriram", Album: "Ee Nagaraniki Emaindi"
- "Samjhawan Unplugged Full Video" → Song: "Samjhawan", Artist: "Arijit Singh", Album: "Humpty Sharma Ki Dulhania"

Respond in this EXACT JSON format:
{{
    "song_name": "Exact Song Name for Spotify",
    "artist_name": "Correct Artist Name",
    "album_name": "Album or Movie Name",
    "confidence": 0.95
}}
"""

        try:
            response = model.generate_content(prompt)
        except Exception as e:
            if "quota" in str(e).lower() or "429" in str(e):
                print(f"Gemini API quota exceeded for URL analysis: {e}")
                GEMINI_QUOTA_EXCEEDED = True
                return None, None, None, 0.0
            else:
                print(f"Gemini API error for URL analysis: {e}")
                return None, None, None, 0.0
        
        # Parse Gemini response
        response_text = response.text.strip()
        print(f"Gemini URL analysis response: {response_text}")
        
        # Extract JSON from response
        import json
        import re
        
        # Try to find JSON in the response
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            try:
                result = json.loads(json_str)
                song_name = result.get('song_name', '').strip()
                artist_name = result.get('artist_name', '').strip()
                album_name = result.get('album_name', '').strip()
                confidence = float(result.get('confidence', 0.0))
                
                if song_name and confidence > 0.5:
                    print(f"Gemini URL analysis result: '{song_name}' by '{artist_name}' from '{album_name}' (confidence: {confidence:.2f})")
                    return song_name, artist_name, album_name, confidence
                else:
                    print(f"Gemini URL analysis: Low confidence or no song name found")
                    return None, None, None, 0.0
                    
            except json.JSONDecodeError as e:
                print(f"Failed to parse Gemini JSON response: {e}")
                return None, None, None, 0.0
        else:
            print("No JSON found in Gemini response")
            return None, None, None, 0.0
            
    except Exception as e:
        print(f"YouTube URL analysis error: {e}")
        return None, None, None, 0.0

def search_youtube_music_for_metadata(original_title, channel_title=None):
    """Search YouTube Music for clean metadata using the original title"""
    try:
        from ytmusicapi import YTMusic
        
        # Initialize YouTube Music API with authentication
        # Try to use existing auth file, or create a new one
        try:
            ytmusic = YTMusic('oauth.json')  # Try existing auth file
        except:
            try:
                ytmusic = YTMusic()  # Try without auth (may have limited access)
            except Exception as e:
                print(f"YouTube Music API initialization failed: {e}")
                return None, None, None, 0.0
        
        # Try multiple search strategies
        search_queries = [
            original_title,  # Original title
            original_title.split(' - ')[-1] if ' - ' in original_title else original_title,  # After dash
            original_title.split(' | ')[0] if ' | ' in original_title else original_title,  # Before pipe
        ]
        
        best_result = None
        best_confidence = 0.0
        
        for query in search_queries:
            if not query.strip():
                continue
                
            print(f"Searching YouTube Music for: '{query}'")
            results = ytmusic.search(query, filter="songs", limit=3)
            
            if not results:
                continue
            
            # Check each result for the best match
            for result in results:
                song_name = result.get('title', '')
                artists = result.get('artists', [])
                album = result.get('album', {}).get('name', '') if result.get('album') else ''
                artist_name = artists[0].get('name', '') if artists else ''
                
                # Calculate confidence based on title similarity
                title_similarity = fuzz.ratio(original_title.lower(), song_name.lower())
                confidence = title_similarity / 100.0
                
                # Prefer results with higher confidence
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_result = {
                        'song_name': song_name,
                        'artist_name': artist_name,
                        'album': album,
                        'confidence': confidence
                    }
        
        if best_result and best_confidence >= 0.5:  # Minimum 50% similarity
            print(f"YouTube Music result: '{best_result['song_name']}' by '{best_result['artist_name']}' from '{best_result['album']}' (confidence: {best_result['confidence']:.2f})")
            return best_result['song_name'], best_result['artist_name'], best_result['album'], best_result['confidence']
        else:
            print("No good matches found on YouTube Music")
            return None, None, None, 0.0
        
    except Exception as e:
        print(f"YouTube Music search error: {e}")
        return None, None, None, 0.0

def analyze_youtube_description(video_id, original_title, channel_title=None):
    """Analyze YouTube video description to extract correct song information, with Groq fallback"""
    # Try Gemini first
    if GEMINI_API_KEY and not GEMINI_QUOTA_EXCEEDED:
        try:
            return analyze_youtube_description_gemini(video_id, original_title, channel_title)
        except Exception as e:
            print(f"Gemini description analysis failed: {e}")
    
    # Fallback to Groq if Gemini fails or quota exceeded
    if GROQ_API_KEY:
        print("Using Groq as fallback for description analysis...")
        return analyze_youtube_description_groq(video_id, original_title, channel_title)
    
    return None, None, None, 0.0

def analyze_youtube_description_gemini(video_id, original_title, channel_title=None):
    """Analyze YouTube video description using Gemini to extract correct song information"""
    if not GEMINI_API_KEY or GEMINI_QUOTA_EXCEEDED:
        return None, None, None, 0.0
    
    try:
        import requests
        
        # Get video description from YouTube API using API key
        video_url = f"https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'snippet',
            'id': video_id,
            'key': YOUTUBE_API_KEY
        }
        
        response = requests.get(video_url, params=params)
        
        if response.status_code != 200:
            print(f"YouTube API error: {response.status_code}")
            return None, None, None, 0.0
        
        data = response.json()
        if not data.get('items'):
            return None, None, None, 0.0
        
        video_info = data['items'][0]['snippet']
        description = video_info.get('description', '')
        video_title = video_info.get('title', original_title)
        
        # Use Gemini to analyze the description
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
You are a music industry expert. Analyze this YouTube video information to extract the correct song details.

Video Title: "{video_title}"
Channel: "{channel_title or 'Unknown'}"
Description: "{description[:1000]}"  # First 1000 chars

TASK: Extract the actual song name, artist, and album from the description and title.

ANALYSIS RULES:
1. Look for the actual song name in the description (often mentioned clearly)
2. Find the real artist/singer (not music directors, actors, or channel names)
3. Identify the album or movie name
4. For Indian movies: Look for singers like "Arijit Singh", "Atif Aslam", "Shreya Ghoshal"
5. Music directors like "Pritam", "A.R. Rahman" are NOT the singers
6. Actor names are usually not the singers

Respond in this EXACT JSON format:
{{
    "song_name": "Actual Song Name",
    "artist_name": "Real Artist/Singer Name", 
    "album_name": "Album or Movie Name",
    "confidence": 0.95
}}

CONFIDENCE SCORING:
- 0.9-1.0: Very confident (clear information in description)
- 0.7-0.9: Confident (good information found)
- 0.5-0.7: Moderate (some uncertainty)
- 0.3-0.5: Low (limited information)
- 0.0-0.3: Very low (guessing)

EXAMPLES:
- Title: "Samjhawan Unplugged Full Video", Description: "Samjhawan song from Humpty Sharma Ki Dulhania" → {{"song_name": "Samjhawan", "artist_name": "Arijit Singh", "album_name": "Humpty Sharma Ki Dulhania", "confidence": 0.9}}
- Title: "Kaifi Khalil", Description: "Kahani Meri by Kaifi Khalil" → {{"song_name": "Kahani Meri", "artist_name": "Kaifi Khalil", "album_name": "Unknown", "confidence": 0.8}}

Use the description to find the most accurate information possible.
"""
        
        try:
            response = model.generate_content(prompt)
        except Exception as e:
            if "quota" in str(e).lower() or "429" in str(e):
                print(f"Gemini API quota exceeded for description analysis: {e}")
                return None, None, None, 0.0
            else:
                print(f"Gemini API error for description analysis: {e}")
                return None, None, None, 0.0
        
        # Parse the JSON response
        try:
            response_text = response.text.strip()
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()
            elif response_text.startswith('```'):
                response_text = response_text.replace('```', '').strip()
            
            result = json.loads(response_text)
            song_name = result.get('song_name', '').strip()
            artist_name = result.get('artist_name', '').strip()
            album_name = result.get('album_name', '').strip()
            confidence = float(result.get('confidence', 0.5))
            
            print(f"YouTube description analysis: '{original_title}' -> Song: '{song_name}', Artist: '{artist_name}', Album: '{album_name}', Confidence: {confidence:.2f}")
            return song_name, artist_name, album_name, confidence
            
        except json.JSONDecodeError as e:
            print(f"Gemini returned invalid JSON for description analysis: {response.text}")
            return None, None, None, 0.0
            
    except Exception as e:
        print(f"YouTube description analysis error: {e}")
        return None, None, None, 0.0

def get_artist_and_album_info(song_name, original_title, channel_title=None):
    """Get artist and album information using Gemini AI with confidence scoring"""
    if not GEMINI_API_KEY or GEMINI_QUOTA_EXCEEDED:
        return None, None, 0.0
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
You are a music industry expert. I need you to find the artist and album information for this song.

Song Name: "{song_name}"
Original YouTube Title: "{original_title}"
Channel Name: "{channel_title or 'Unknown'}"

TASK: Find the actual artist/singer and album name for this song.

SEARCH INSTRUCTIONS:
1. Search for the song using the song name and original YouTube title
2. Find the real artist/singer (not music directors, channel names, or actors)
3. Find the album name or movie name
4. For titles like "Movie Name - Song Name", the movie name is the album
5. Look for patterns like "Movie Name - Song Name | Artist" to identify components
6. For Indian movies: Look for singers like "Arijit Singh", "Karthik", "Shreya Ghoshal", "Sid Sriram"
7. Music directors like "DSP", "Devi Sri Prasad", "A.R. Rahman" are NOT the singers
8. Actor names like "Prabhas", "Kajal Aggarwal" are usually not the singers

Respond in this EXACT JSON format:
{{
    "artist_name": "Real Artist/Singer Name",
    "album_name": "Album or Movie Name",
    "confidence": 0.95
}}

CONFIDENCE SCORING:
- 0.9-1.0: Very confident (exact match found, multiple sources confirm)
- 0.7-0.9: Confident (good match, some uncertainty)
- 0.5-0.7: Moderate (partial match, some ambiguity)
- 0.3-0.5: Low (uncertain, limited information)
- 0.0-0.3: Very low (guessing, high uncertainty)

EXAMPLES:
- Song: "Jeena Jeena", Title: "UNPLUGGED Full Audio Song – Jeena Jeena by Sachin - Jigar" → {{"artist_name": "Atif Aslam", "album_name": "Badlapur", "confidence": 0.95}}
- Song: "Ae Dil Hai Mushkil", Title: "Ae Dil Hai Mushkil Title Track Full Video" → {{"artist_name": "Arijit Singh", "album_name": "Ae Dil Hai Mushkil", "confidence": 0.9}}
- Song: "Baarish Ki Jaaye", Title: "Baarish Ki Jaaye | B Praak Ft Nawazuddin Siddiqui" → {{"artist_name": "B Praak", "album_name": "Baarish Ki Jaaye", "confidence": 0.85}}
- Song: "Bhaga Bhaga", Title: "Saripodhaa Sanivaaram - Bhaga Bhaga Lyrical | Nani | Priyanka Mohan" → {{"artist_name": "Sid Sriram", "album_name": "Saripodhaa Sanivaaram", "confidence": 0.9}}

Use web search to find the most accurate information possible.
"""
        
        try:
            response = model.generate_content(prompt)
        except Exception as e:
            if "quota" in str(e).lower() or "429" in str(e):
                print(f"Gemini API quota exceeded for artist/album search: {e}")
                return None, None, 0.0
            else:
                print(f"Gemini API error for artist/album search: {e}")
                return None, None, 0.0
        
        # Parse the JSON response
        try:
            response_text = response.text.strip()
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()
            elif response_text.startswith('```'):
                response_text = response_text.replace('```', '').strip()
            
            result = json.loads(response_text)
            artist_name = result.get('artist_name', '').strip()
            album_name = result.get('album_name', '').strip()
            confidence = float(result.get('confidence', 0.5))
            
            print(f"Gemini artist/album search: '{song_name}' -> Artist: '{artist_name}', Album: '{album_name}', Confidence: {confidence:.2f}")
            return artist_name, album_name, confidence
            
        except json.JSONDecodeError as e:
            print(f"Gemini returned invalid JSON for artist/album: {response.text}")
            return None, None, 0.0
            
    except Exception as e:
        print(f"Gemini API error for artist/album: {e}")
        return None, None, 0.0

def advanced_fuzzy_match(song_title, artist_name, spotify_track):
    """Advanced fuzzy matching using multiple algorithms"""
    spotify_title = spotify_track['name'].lower()
    spotify_artist = spotify_track['artists'][0]['name'].lower()
    
    song_title_lower = song_title.lower()
    artist_name_lower = artist_name.lower() if artist_name else ""
    
    # 1. Token Set Ratio (ignores word order and duplicates)
    title_token_ratio = fuzz.token_set_ratio(song_title_lower, spotify_title)
    artist_token_ratio = fuzz.token_set_ratio(artist_name_lower, spotify_artist) if artist_name else 0
    
    # 2. Partial Ratio (handles partial matches)
    title_partial_ratio = fuzz.partial_ratio(song_title_lower, spotify_title)
    artist_partial_ratio = fuzz.partial_ratio(artist_name_lower, spotify_artist) if artist_name else 0
    
    # 3. Simple Ratio (exact matching)
    title_simple_ratio = fuzz.ratio(song_title_lower, spotify_title)
    artist_simple_ratio = fuzz.ratio(artist_name_lower, spotify_artist) if artist_name else 0
    
    # 4. Contains match bonus
    title_contains = 1 if song_title_lower in spotify_title or spotify_title in song_title_lower else 0
    artist_contains = 1 if artist_name_lower in spotify_artist or spotify_artist in artist_name_lower else 0
    
    # 5. Channel-based confidence boost
    channel_boost = 0.1 if spotify_track.get('album', {}).get('name', '').lower() in ['t-series', 'sony music', 'zee music'] else 0
    
    # CRITICAL FIX: Use simple ratio as PRIMARY metric
    # Simple ratio is most accurate for exact matches
    title_score = title_simple_ratio / 100  # Use simple ratio directly
    artist_score = artist_simple_ratio / 100 if artist_name else 0.5
    
    # HEAVY PENALTY for different titles
    if title_simple_ratio < 60:  # Less than 60% similarity
        title_score *= 0.5  # Heavy penalty
    
    if title_simple_ratio < 40:  # Less than 40% similarity  
        title_score *= 0.2  # Very heavy penalty
        
    if title_simple_ratio < 20:  # Less than 20% similarity
        title_score *= 0.05  # Extreme penalty
    
    # Weighted composite score
    composite_score = (
        title_score * 0.6 +  # Title is more important
        artist_score * 0.3 +  # Artist is important but less so
        title_contains * 0.05 +  # Contains match bonus
        artist_contains * 0.05 +  # Contains match bonus
        channel_boost  # Channel confidence boost
    )
    
    return {
        'composite_score': composite_score,
        'title_score': title_score,
        'artist_score': artist_score,
        'title_token_ratio': title_token_ratio,
        'artist_token_ratio': artist_token_ratio,
        'title_partial_ratio': title_partial_ratio,
        'artist_partial_ratio': artist_partial_ratio,
        'contains_match': title_contains or artist_contains
    }

def calculate_confidence_score(gemini_confidence, fuzzy_scores, search_strategy, channel_name=None):
    """Calculate overall confidence score based on multiple factors"""
    
    # Base confidence from Gemini
    base_confidence = gemini_confidence or 0.5
    
    # Fuzzy matching score
    fuzzy_confidence = fuzzy_scores.get('composite_score', 0.0)
    
    # Search strategy multiplier
    strategy_multipliers = {
        'artist': 1.2,    # Artist search is most reliable
        'album': 1.1,     # Album search is good
        'song_only': 0.9  # Song-only search is less reliable
    }
    strategy_multiplier = strategy_multipliers.get(search_strategy, 1.0)
    
    # Channel confidence boost
    trusted_channels = ['t-series', 'sony music', 'zee music', 'tips music', 'venus music']
    channel_boost = 0.1 if channel_name and any(channel in channel_name.lower() for channel in trusted_channels) else 0
    
    # Calculate weighted confidence
    overall_confidence = (
        base_confidence * 0.4 +  # Gemini confidence
        fuzzy_confidence * 0.4 +  # Fuzzy matching
        min(fuzzy_confidence + 0.2, 1.0) * 0.2  # Bonus for good fuzzy match
    ) * strategy_multiplier + channel_boost
    
    return min(overall_confidence, 1.0)  # Cap at 1.0

def parse_youtube_title_fallback(title, channel_title=None):
    """Fallback regex-based parser when Gemini is not available"""
    if not title:
        return "Unknown Title", "Unknown Artist"
    
    print(f"Fallback parsing: '{title}'")
    
    # Simple regex-based parsing as fallback
    title = title.strip()
    
    # Remove common video descriptors
    video_descriptors = [
        'official video', 'official music video', 'lyrics video', 'lyrics',
        '4k', 'hd', 'hq', 'full song', 'complete song', 'extended',
        'remix', 'cover', 'acoustic', 'live', 'studio version',
        'with lyrics', 'lyrics video', 'music video', 'mv',
        'tribute to', 'song with lyrics', 'songs', 'song', 'full video song'
    ]
    
    # Remove video descriptors (case insensitive)
    for descriptor in video_descriptors:
        title = re.sub(rf'\s*-\s*{re.escape(descriptor)}\s*$', '', title, flags=re.IGNORECASE)
        title = re.sub(rf'\s*\({re.escape(descriptor)}\)', '', title, flags=re.IGNORECASE)
        title = re.sub(rf'\s*\[{re.escape(descriptor)}\]', '', title, flags=re.IGNORECASE)
    
    # Handle specific patterns
    # Pattern 1: "Artist - Song Name [Official Music Video]"
    if ' - ' in title and '[' in title:
        parts = title.split(' - ', 1)
        if len(parts) == 2:
            artist_name = parts[0].strip()
            song_part = parts[1].strip()
            # Remove brackets and their contents
            song_name = re.sub(r'\s*\[.*?\]', '', song_part)
            print(f"Pattern 1 match: Artist='{artist_name}', Song='{song_name}'")
            return song_name, artist_name
    
    # Pattern 2: "Song Name | Movie Name | Artist | Music Director"
    if ' | ' in title:
        parts = [part.strip() for part in title.split(' | ')]
        if len(parts) >= 3:
            # First part is usually the song name
            song_name = parts[0].strip()
            # Look for artist in the parts (usually contains names)
            artist_name = "Unknown Artist"
            for part in parts[1:]:
                # Skip movie names and technical terms
                if not any(word in part.lower() for word in ['movie', 'film', 'video', 'song', 'music', 'director', 'composer']):
                    if len(part.split()) <= 3:  # Likely a person's name
                        artist_name = part
                        break
            print(f"Pattern 2 match: Song='{song_name}', Artist='{artist_name}'")
            return song_name, artist_name
    
    # Pattern 3: "Artist - Song Name" (simple dash)
    if ' - ' in title:
        parts = [part.strip() for part in title.split(' - ', 1)]
        if len(parts) == 2:
            artist_name = parts[0].strip()
            song_name = parts[1].strip()
            print(f"Pattern 3 match: Artist='{artist_name}', Song='{song_name}'")
            return song_name, artist_name
    
    # Pattern 4: "Song Name by Artist"
    if ' by ' in title.lower():
        parts = title.split(' by ', 1)
        if len(parts) == 2:
            song_name = parts[0].strip()
            artist_name = parts[1].strip()
            print(f"Pattern 4 match: Song='{song_name}', Artist='{artist_name}'")
            return song_name, artist_name
    
    # If no pattern matches, return the cleaned title as song name
    print(f"No pattern match, using title as song: '{title.strip()}'")
    return title.strip(), (channel_title or "Unknown Artist")

def create_youtube_playlist_api(access_token, title, description):
    """Create a new YouTube playlist"""
    try:
        import requests
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'snippet': {
                'title': title,
                'description': description,
                'defaultLanguage': 'en'
            },
            'status': {
                'privacyStatus': 'private'
            }
        }
        
        response = requests.post(
            'https://www.googleapis.com/youtube/v3/playlists?part=snippet,status',
            headers=headers,
            data=json.dumps(data)
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error creating YouTube playlist: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"Error creating YouTube playlist: {e}")
        return None

def update_spotify_playlist(access_token, playlist, songs_to_add):
    """Update a Spotify playlist with new songs"""
    print(f"=== update_spotify_playlist CALLED ===")
    print(f"Playlist: {playlist.name}")
    print(f"Songs to add: {len(songs_to_add)}")
    
    # Log to file for better debugging
    with open('/tmp/sync_debug.log', 'a') as f:
        f.write(f"=== update_spotify_playlist CALLED ===\n")
        f.write(f"Playlist: {playlist.name}\n")
        f.write(f"Songs to add: {len(songs_to_add)}\n")
    
    try:
        sp = spotipy.Spotify(auth=access_token)
        songs_added = 0
        
        for song_info in songs_to_add:
            try:
                print(f"Searching Spotify for: '{song_info['title']}' by '{song_info['artist']}'")
                
                # Systematic search approach: Try artist first, then album, then song name only
                search_strategies = []
                
                # Strategy 1: Search with artist name
                if song_info.get('artist'):
                    search_strategies.append({
                        'name': 'artist',
                        'queries': [
                            f'track:"{song_info["title"]}" artist:"{song_info["artist"]}"',
                            f'track:{song_info["title"]} artist:{song_info["artist"]}',
                            f'"{song_info["title"]}" "{song_info["artist"]}"',
                            f'{song_info["title"]} {song_info["artist"]}'
                        ]
                    })
                
                # Strategy 2: Search with album name
                if song_info.get('album'):
                    search_strategies.append({
                        'name': 'album',
                        'queries': [
                            f'track:"{song_info["title"]}" album:"{song_info["album"]}"',
                            f'track:{song_info["title"]} album:{song_info["album"]}',
                            f'"{song_info["title"]}" "{song_info["album"]}"',
                            f'{song_info["title"]} {song_info["album"]}',
                            f'"{song_info["album"]}" "{song_info["title"]}"',  # Album first
                            f'{song_info["album"]} {song_info["title"]}'  # Album first
                        ]
                    })
                
                # Strategy 3: Search with song name only (improved queries)
                search_strategies.append({
                    'name': 'song_only',
                    'queries': [
                        f'track:"{song_info["title"]}"',
                        f'track:{song_info["title"]}',
                        f'"{song_info["title"]}"',
                        f'{song_info["title"]} song',
                        f'{song_info["title"]} music',
                        f'{song_info["title"]} audio',
                        # Add more specific queries for better results
                        f'{song_info["title"]} bollywood',
                        f'{song_info["title"]} hindi',
                        f'{song_info["title"]} telugu',
                        f'{song_info["title"]} tamil',
                        f'{song_info["title"]} punjabi'
                    ]
                })
                
                # Try each strategy in order
                results = None
                used_strategy = None
                used_query = None
                
                for strategy in search_strategies:
                    print(f"Trying {strategy['name']} strategy...")
                    for query in strategy['queries']:
                        print(f"  Query: {query}")
                        results = sp.search(q=query, type='track', limit=1)
                        if results['tracks']['items']:
                            used_strategy = strategy['name']
                            used_query = query
                            break
                    if results and results['tracks']['items']:
                        break
                
                print(f"Search results: {len(results['tracks']['items'])} tracks found using {used_strategy} strategy: {used_query}")
                
                if results['tracks']['items']:
                    track = results['tracks']['items'][0]
                    track_uri = track['uri']
                    print(f"Found track: {track['name']} by {track['artists'][0]['name']} - URI: {track_uri}")
                    
                    # Advanced fuzzy matching
                    fuzzy_scores = advanced_fuzzy_match(
                        song_info['title'], 
                        song_info.get('artist'), 
                        track
                    )
                    
                    # Calculate overall confidence score
                    overall_confidence = calculate_confidence_score(
                        song_info.get('gemini_confidence', 0.5),
                        fuzzy_scores,
                        used_strategy,
                        song_info.get('channel_name')
                    )
                    
                    # Confidence-based triage (STRICTER THRESHOLDS)
                    if overall_confidence >= 0.90:  # Increased from 0.85
                        match_quality = "HIGH"
                        is_good_match = True
                    elif overall_confidence >= 0.80:  # Increased from 0.7
                        match_quality = "MEDIUM"
                        is_good_match = True
                    elif overall_confidence >= 0.5:  # Increased from 0.4
                        match_quality = "LOW"
                        is_good_match = False  # Needs user confirmation
                    else:
                        match_quality = "VERY_LOW"
                        is_good_match = False  # Needs user confirmation
                    
                    print(f"Advanced validation ({used_strategy}): '{song_info['title']}' vs '{track['name']}'")
                    print(f"Fuzzy scores: {fuzzy_scores}")
                    print(f"Title similarity: {fuzzy_scores.get('title_simple_ratio', 0)}% simple, {fuzzy_scores.get('title_token_ratio', 0)}% token")
                    print(f"Overall confidence: {overall_confidence:.3f} ({match_quality})")
                    print(f"Good match: {is_good_match}")
                    
                    if is_good_match:
                        # Auto-add good matches
                        spotify_playlist_id = playlist.platform_playlist_id
                        if spotify_playlist_id:
                            print(f"Auto-adding good match: {track['name']}")
                            sp.playlist_add_items(spotify_playlist_id, [track_uri])
                            songs_added += 1
                            print(f"Successfully added '{song_info['title']}' to Spotify playlist")
                            
                            # Log success to file
                            with open('/tmp/sync_debug.log', 'a') as f:
                                f.write(f"Auto-added good match: '{song_info['title']}' -> '{track['name']}'\n")
                    
                        # Store user feedback for learning
                        if song_info.get('original_title'):
                            feedback = UserFeedback(
                                user_id=current_user.user_id,
                                original_youtube_title=song_info['original_title'],
                                original_channel=song_info.get('channel_name'),
                                corrected_song_name=track['name'],
                                corrected_artist=track['artists'][0]['name'],
                                corrected_album=track['album']['name'],
                                spotify_uri=track['uri'],
                                confidence_score=overall_confidence,
                                feedback_type='confirmation'
                            )
                            db.session.add(feedback)
                            db.session.commit()
                        continue
                else:
                    print(f"Found track but poor match: '{track['name']}' vs '{song_info['title']}' - trying fallback search")
                    # Store poor match for user confirmation
                    if song_info.get('original_title'):
                        # Store in session for user confirmation
                        if 'pending_tracks' not in session:
                            session['pending_tracks'] = []
                        
                        # Calculate title similarity for user comparison
                        original_title = song_info.get('original_title', song_info['title'])
                        spotify_title = track['name']
                        title_similarity = fuzz.ratio(original_title.lower(), spotify_title.lower())
                        
                        session['pending_tracks'].append({
                                'song_info': song_info,
                                'spotify_track': track,
                                'confidence': overall_confidence,
                                'search_strategy': 'poor_match',
                                'fuzzy_scores': {
                                    'title_simple_ratio': fuzz.ratio(song_info['title'].lower(), track['name'].lower()),
                                    'title_token_ratio': fuzz.token_set_ratio(song_info['title'].lower(), track['name'].lower()),
                                    'artist_simple_ratio': fuzz.ratio(song_info.get('artist', '').lower(), track['artists'][0]['name'].lower()) if song_info.get('artist') else 0
                                },
                                'title_comparison': {
                                    'original_youtube_title': original_title,
                                    'spotify_title': spotify_title,
                                    'similarity_percentage': title_similarity,
                                    'is_similar': title_similarity >= 50
                                }
                            })
                        session.modified = True
                        print(f"Stored poor match for user confirmation: {track['name']}")
                        # Continue to fallback search
                    else:
                        print(f"No Spotify track found for: {song_info['title']} by {song_info['artist']}")
                    
                    # Try fallback search with Gemini re-analysis of full YouTube title
                    print(f"All strategies failed, asking Gemini to re-analyze full YouTube title...")
                    
                    # Initialize pending_tracks for fallback results
                    if 'pending_tracks' not in session:
                        session['pending_tracks'] = []
                    pending_tracks = session['pending_tracks']
                    
                    # Get the original YouTube title for re-analysis
                    original_title = song_info.get('original_title', song_info['title'])
                    channel_name = song_info.get('channel_name', 'Unknown')
                    
                    # Ask Gemini to extract the correct song name from the full YouTube title
                    corrected_song_name, _ = parse_youtube_title_with_gemini(original_title, channel_name)
                    
                    print(f"Gemini re-analysis: '{original_title}' -> '{corrected_song_name}'")
                    
                    # Also try Gemini with YouTube URL for more accurate results
                    video_id = song_info.get('video_id')
                    if video_id:
                        print(f"Trying Gemini with YouTube URL for more accurate results...")
                        url_song_name, url_artist_name, url_album_name, url_confidence = get_spotify_song_name_from_youtube_url(
                            video_id, original_title, channel_name
                        )
                        
                        if url_song_name and url_confidence >= 0.6:
                            print(f"Gemini URL analysis found better result: '{url_song_name}' (confidence: {url_confidence:.2f})")
                            corrected_song_name = url_song_name  # Use the more accurate result
                    
                    # Now search Spotify with the corrected song name using more targeted queries
                    fallback_queries = [
                        f'track:"{corrected_song_name}"',  # Exact phrase match
                        f'"{corrected_song_name}"',        # Phrase search
                        f'track:{corrected_song_name}',    # Standard search
                        f'{corrected_song_name}',          # Simple search
                    ]
                    
                    # Add artist-specific searches if we have artist info
                    if song_info.get('artist') and song_info['artist'] != 'Unknown':
                        artist_name = song_info['artist']
                        fallback_queries.extend([
                            f'track:"{corrected_song_name}" artist:"{artist_name}"',
                            f'"{corrected_song_name}" "{artist_name}"',
                            f'{corrected_song_name} {artist_name}',
                        ])
                    
                    # Add album-specific searches if we have album info
                    if song_info.get('album') and song_info['album'] != 'Unknown':
                        album_name = song_info['album']
                        fallback_queries.extend([
                            f'track:"{corrected_song_name}" album:"{album_name}"',
                            f'"{corrected_song_name}" "{album_name}"',
                        ])
                    
                    fallback_results = None
                    used_fallback_query = None
                    
                    for query in fallback_queries:
                        print(f"Trying fallback query: {query}")
                        fallback_results = sp.search(q=query, type='track', limit=10)  # Get more results
                        if fallback_results['tracks']['items']:
                            used_fallback_query = query
                            # Don't break immediately - try to get more diverse results
                            if len(fallback_results['tracks']['items']) >= 5:
                                break
                    
                    print(f"Fallback search results: {len(fallback_results['tracks']['items'])} tracks found using query: {used_fallback_query}")
                    
                    if fallback_results['tracks']['items']:
                        print(f"Fallback search found {len(fallback_results['tracks']['items'])} tracks")
                        
                        # Find the best fallback matches using advanced fuzzy matching
                        fallback_tracks = []
                        
                        for track in fallback_results['tracks']['items']:
                            # Use the same advanced fuzzy matching as main search
                            fuzzy_scores = advanced_fuzzy_match(corrected_song_name, song_info.get('artist', ''), track)
                            
                            # Calculate confidence using the same method as main search
                            fallback_confidence = calculate_confidence_score(
                                song_info.get('gemini_confidence', 0.5),
                                fuzzy_scores,
                                'song_only',  # Fallback is always song-only search
                                song_info.get('channel_name')
                            )
                            
                            print(f"Fallback validation: '{corrected_song_name}' vs '{track['name']}'")
                            print(f"Fuzzy scores: {fuzzy_scores}")
                            print(f"Fallback confidence: {fallback_confidence:.3f}")
                            
                            # Only include tracks with reasonable similarity
                            if fuzzy_scores.get('title_simple_ratio', 0) >= 20:  # At least 20% similarity
                                fallback_tracks.append({
                                    'track': track,
                                    'confidence': fallback_confidence,
                                    'fuzzy_scores': fuzzy_scores
                                })
                        
                        # Sort by confidence and take top 3
                        fallback_tracks.sort(key=lambda x: x['confidence'], reverse=True)
                        fallback_tracks = fallback_tracks[:3]  # Top 3 most relevant
                        
                        # Store fallback results for user confirmation
                        if fallback_tracks:
                            print(f"Found {len(fallback_tracks)} relevant fallback tracks")
                            for i, fallback_data in enumerate(fallback_tracks):
                                track = fallback_data['track']
                                confidence = fallback_data['confidence']
                                print(f"Fallback {i+1}: '{track['name']}' by {track['artists'][0]['name']} (confidence: {confidence:.3f})")
                                
                                # Calculate title similarity for user comparison
                                original_title = song_info.get('original_title', song_info['title'])
                                spotify_title = track['name']
                                title_similarity = fuzz.ratio(original_title.lower(), spotify_title.lower())
                                
                                # Add to pending tracks for user confirmation
                                pending_tracks.append({
                                    'song_info': song_info,
                                    'spotify_track': track,
                                    'confidence': confidence,
                                    'search_strategy': 'fallback',
                                    'fuzzy_scores': fallback_data['fuzzy_scores'],
                                    'title_comparison': {
                                        'original_youtube_title': original_title,
                                        'spotify_title': spotify_title,
                                        'similarity_percentage': title_similarity,
                                        'is_similar': title_similarity >= 50  # 50%+ similarity
                                    }
                                })
                        else:
                            print("No relevant fallback tracks found - will skip this song")
                            # Add to pending tracks as "no match found"
                            pending_tracks.append({
                                'song_info': song_info,
                                'spotify_track': None,
                                'confidence': 0.0,
                                'search_strategy': 'no_match',
                                'fuzzy_scores': {}
                            })
                    
            except Exception as song_error:
                print(f"Error adding song '{song_info['title']}' to Spotify: {song_error}")
                
                # Log error to file
                with open('/tmp/sync_debug.log', 'a') as f:
                    f.write(f"Error adding song '{song_info['title']}' to Spotify: {song_error}\n")
                continue
        
        return songs_added
        
    except Exception as e:
        print(f"Error updating Spotify playlist: {e}")
        return 0

def update_youtube_playlist(access_token, playlist, songs_to_add):
    """Update a YouTube playlist with new songs (simplified version)"""
    print(f"=== update_youtube_playlist CALLED ===")
    print(f"Playlist: {playlist.name}")
    print(f"Songs to add: {len(songs_to_add)}")
    
    # Log to file
    with open('/tmp/sync_debug.log', 'a') as f:
        f.write(f"=== update_youtube_playlist CALLED ===\n")
        f.write(f"Playlist: {playlist.name}\n")
        f.write(f"Songs to add: {len(songs_to_add)}\n")
    
    try:
        import requests
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        youtube_playlist_id = playlist.platform_playlist_id
        if not youtube_playlist_id:
            print(f"ERROR: No YouTube playlist ID found for playlist '{playlist.name}'")
            return 0
        
        songs_added = 0
        search_url = "https://www.googleapis.com/youtube/v3/search"
        
        for song_info in songs_to_add:
            try:
                # Search for the song on YouTube
                search_params = {
                    'part': 'snippet',
                    'q': f"{song_info['title']} {song_info['artist']}",
                    'type': 'video',
                    'maxResults': 1
                }
                
                search_response = requests.get(search_url, headers=headers, params=search_params)
                print(f"YouTube search response for '{song_info['title']}': {search_response.status_code}")
                
                if search_response.status_code == 200:
                    search_data = search_response.json()
                    
                    if search_data.get('items'):
                        video_id = search_data['items'][0]['id']['videoId']
                        print(f"Found YouTube video ID: {video_id} for '{song_info['title']}'")
                        
                        # Add video to playlist
                        add_data = {
                            'snippet': {
                                'playlistId': youtube_playlist_id,
                                'resourceId': {
                                    'kind': 'youtube#video',
                                    'videoId': video_id
                                }
                            }
                        }
                        
                        add_response = requests.post(
                            'https://www.googleapis.com/youtube/v3/playlistItems?part=snippet',
                            headers=headers,
                            data=json.dumps(add_data)
                        )
                        
                        print(f"YouTube add to playlist response: {add_response.status_code}")
                        if add_response.status_code == 200:
                            songs_added += 1
                            print(f"Added '{song_info['title']}' to YouTube playlist")
                        else:
                            print(f"Failed to add '{song_info['title']}' to YouTube playlist: {add_response.text}")
                    else:
                        print(f"No YouTube video found for: {song_info['title']} by {song_info['artist']}")
                else:
                    print(f"YouTube search failed for: {song_info['title']} - {search_response.text}")
                    
            except Exception as song_error:
                print(f"Error adding song '{song_info['title']}' to YouTube: {song_error}")
                continue
        
        return songs_added
        
    except Exception as e:
        print(f"Error updating YouTube playlist: {e}")
        return 0

def sync_playlist_cross_platform(source_playlist, target_playlist, source_platform, target_platform, user_accounts):
    """Sync playlist from one platform to another (e.g., YouTube to Spotify)"""
    try:
        # Get source and target account tokens
        source_account = None
        target_account = None
        
        for account in user_accounts:
            platform = db.session.get(Platform, account.platform_id)
            if platform.platform_name == source_platform:
                source_account = account
            elif platform.platform_name == target_platform:
                target_account = account
        
        if not source_account or not target_account:
            return False, "Missing platform connections"
        
        # Get songs from source playlist
        source_songs = []
        playlist_songs = PlaylistSong.query.filter_by(playlist_id=source_playlist.playlist_id).all()
        
        for ps in playlist_songs:
            song = db.session.get(Song, ps.song_id)
            if song:
                source_songs.append({
                    'title': song.title,
                    'artist': song.artist,
                    'album': song.album,
                    'duration': song.duration
                })
        
        # Add songs to target platform
        songs_added = 0
        if target_platform == 'Spotify':
            songs_added = update_spotify_playlist(target_account.auth_token, target_playlist, source_songs)
        elif target_platform == 'YouTube':
            songs_added = update_youtube_playlist(target_account.auth_token, target_playlist, source_songs)
        
        return True, f"Successfully synced {songs_added} songs from {source_platform} to {target_platform}"
        
    except Exception as e:
        return False, f"Error syncing playlist: {str(e)}"

# Database Models
class User(UserMixin, db.Model):
    __tablename__ = 'User_'
    user_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    platform_accounts = db.relationship('UserPlatformAccount', backref='user', lazy=True)
    sync_logs = db.relationship('SyncLog', backref='user', lazy=True)
    
    def get_id(self):
        return str(self.user_id)

class Admin(UserMixin, db.Model):
    admin_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    
    def get_id(self):
        return str(self.admin_id)

class Platform(db.Model):
    platform_id = db.Column(db.Integer, primary_key=True)
    platform_name = db.Column(db.String(100), nullable=False)
    api_details = db.Column(db.Text)
    user_accounts = db.relationship('UserPlatformAccount', backref='platform', lazy=True)
    platform_songs = db.relationship('PlatformSong', backref='platform', lazy=True)

class UserPlatformAccount(db.Model):
    account_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('User_.user_id'), nullable=False)
    platform_id = db.Column(db.Integer, db.ForeignKey('platform.platform_id'), nullable=False)
    username_on_platform = db.Column(db.String(100))
    auth_token = db.Column(db.Text)
    playlists = db.relationship('Playlist', backref='account', lazy=True)

class Playlist(db.Model):
    playlist_id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('user_platform_account.account_id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500))
    last_updated = db.Column(db.Date, default=lambda: datetime.now().date())
    platform_playlist_id = db.Column(db.String(200))
    playlist_songs = db.relationship('PlaylistSong', backref='playlist', lazy=True)
    sync_logs = db.relationship('SyncLog', backref='playlist', lazy=True)

class Song(db.Model):
    song_id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(150))
    album = db.Column(db.String(150))
    duration = db.Column(db.Integer)
    playlist_songs = db.relationship('PlaylistSong', backref='song', lazy=True)
    platform_songs = db.relationship('PlatformSong', backref='song', lazy=True)

class PlatformSong(db.Model):
    platform_song_id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.song_id'), nullable=False)
    platform_id = db.Column(db.Integer, db.ForeignKey('platform.platform_id'), nullable=False)
    platform_specific_id = db.Column(db.String(200))

class PlaylistSong(db.Model):
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.playlist_id'), primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.song_id'), primary_key=True)
    added_at = db.Column(db.Date, default=lambda: datetime.now().date())

class SyncLog(db.Model):
    sync_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('User_.user_id'), nullable=False)
    source_account_id = db.Column(db.Integer, db.ForeignKey('user_platform_account.account_id'), nullable=False)
    destination_account_id = db.Column(db.Integer, db.ForeignKey('user_platform_account.account_id'), nullable=False)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.playlist_id'), nullable=False)
    total_songs_synced = db.Column(db.Integer)
    songs_added = db.Column(db.Integer)
    songs_removed = db.Column(db.Integer)
    timestamp = db.Column(db.Date, default=lambda: datetime.now().date())

class SyncSong(db.Model):
    """Table to track exactly which songs were synced in each sync operation"""
    __tablename__ = 'sync_song'
    sync_id = db.Column(db.Integer, db.ForeignKey('sync_log.sync_id'), primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.song_id'), primary_key=True)
    action = db.Column(db.String(10), nullable=False)  # 'added' or 'removed'
    timestamp = db.Column(db.DateTime, default=datetime.now)

class UserFeedback(db.Model):
    """Table to store user corrections for machine learning"""
    __tablename__ = 'user_feedback'
    feedback_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('User_.user_id'), nullable=False)
    original_youtube_title = db.Column(db.Text, nullable=False)
    original_channel = db.Column(db.String(200))
    corrected_song_name = db.Column(db.String(200), nullable=False)
    corrected_artist = db.Column(db.String(200), nullable=False)
    corrected_album = db.Column(db.String(200))
    spotify_uri = db.Column(db.String(200))
    confidence_score = db.Column(db.Float, default=0.0)
    feedback_type = db.Column(db.String(20), nullable=False)  # 'correction', 'confirmation', 'rejection'
    timestamp = db.Column(db.DateTime, default=datetime.now)
    used_for_training = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    # Try to load regular user first
    user = db.session.get(User, int(user_id))
    if user:
        return user
    # Then try to load admin
    admin = db.session.get(Admin, int(user_id))
    if admin:
        return admin
    return None

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        # Try to find user
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        
        # Try to find admin
        admin = Admin.query.filter_by(email=email).first()
        if admin and check_password_hash(admin.password, password):
            login_user(admin)
            return redirect(url_for('admin_dashboard'))
        
        flash('Invalid email or password')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists')
            return render_template('register.html')
        
        user = User(
            name=name,
            email=email,
            password=generate_password_hash(password)
        )
        
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    # Check if current user is admin - redirect to admin dashboard
    if hasattr(current_user, 'admin_id'):
        return redirect(url_for('admin_dashboard'))
    
    # Get user's platform accounts with platform information (only those with valid tokens)
    user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).filter(UserPlatformAccount.auth_token.isnot(None)).all()
    
    # Add platform information to user accounts
    for account in user_accounts:
        account.platform = db.session.get(Platform, account.platform_id)
    
    # Get playlists for all user accounts with platform and account information
    playlists = []
    for account in user_accounts:
        # Get platform information for this account
        platform = db.session.get(Platform, account.platform_id)
        
        account_playlists = Playlist.query.filter_by(account_id=account.account_id).all()
        for playlist in account_playlists:
            # Count songs in each playlist
            song_count = PlaylistSong.query.filter_by(playlist_id=playlist.playlist_id).count()
            playlist.song_count = song_count
            
            # Add platform and account information to playlist object
            playlist.platform_name = platform.platform_name if platform else "Unknown"
            playlist.account_username = account.username_on_platform if account.username_on_platform else f"user_{account.user_id}"
            
            playlists.append(playlist)
    
    return render_template('dashboard.html', playlists=playlists, user_accounts=user_accounts)

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    """Simple admin dashboard"""
    if not hasattr(current_user, 'admin_id'):
        return redirect(url_for('dashboard'))
    
    # Get basic statistics
    total_users = User.query.count()
    total_playlists = Playlist.query.count()
    total_songs = Song.query.count()
    total_syncs = SyncLog.query.count()
    
    # Get recent activity
    users = User.query.all()
    sync_logs = SyncLog.query.order_by(SyncLog.timestamp.desc()).limit(10).all()
    
    return render_template('admin_dashboard.html', 
                         users=users, 
                         sync_logs=sync_logs,
                         total_users=total_users,
                         total_playlists=total_playlists,
                         total_songs=total_songs,
                         total_syncs=total_syncs)

@app.route('/connect_platform', methods=['GET', 'POST'])
@login_required
def connect_platform():
    # Check if current user is admin - redirect to admin dashboard
    if hasattr(current_user, 'admin_id'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        platform_name = request.form['platform']
        
        if platform_name == 'Spotify':
            # Redirect to Spotify OAuth
            spotify_oauth = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope='playlist-read-private playlist-read-collaborative user-read-private playlist-modify-public playlist-modify-private'
            )
            auth_url = spotify_oauth.get_authorize_url()
            return redirect(auth_url)
        
        elif platform_name == 'YouTube':
            # Redirect to Google OAuth for YouTube
            try:
                platform = Platform.query.filter_by(platform_name='YouTube').first()
                if not platform:
                    platform = Platform(platform_name='YouTube', api_details='{"api_url": "https://www.youtube.com", "version": "v3"}')
                    db.session.add(platform)
                    db.session.commit()
                
                # Build Google OAuth URL
                from urllib.parse import urlencode
                params = {
                    'client_id': YOUTUBE_CLIENT_ID,
                    'redirect_uri': YOUTUBE_REDIRECT_URI,
                    'scope': 'https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.force-ssl',
                    'response_type': 'code',
                    'access_type': 'offline'
                }
                auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
                return redirect(auth_url)
                
            except Exception as e:
                flash(f'Error setting up YouTube connection: {str(e)}')
                return redirect(url_for('dashboard'))
    
    # GET request - show available platforms
    # Ensure platforms exist in database
    spotify_platform = Platform.query.filter_by(platform_name='Spotify').first()
    if not spotify_platform:
        spotify_platform = Platform(platform_name='Spotify', api_details='{"api_url": "https://api.spotify.com"}')
        db.session.add(spotify_platform)
    
    youtube_platform = Platform.query.filter_by(platform_name='YouTube').first()
    if not youtube_platform:
        youtube_platform = Platform(platform_name='YouTube', api_details='{"api_url": "https://www.youtube.com"}')
        db.session.add(youtube_platform)
    
    db.session.commit()
    
    # Get all platforms and user accounts
    all_platforms = Platform.query.all()
    user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).all()
    
    # Create a mapping of platform_id to user account for quick lookup
    account_by_platform = {}
    for account in user_accounts:
        account.platform = db.session.get(Platform, account.platform_id)
        account_by_platform[account.platform_id] = account
    
    # Create platforms data structure with connection status
    platforms = []
    for platform in all_platforms:
        connected_account = account_by_platform.get(platform.platform_id)
        
        # Check if account exists AND has valid auth token
        is_connected = False
        if connected_account and connected_account.auth_token:
            is_connected = True
        elif connected_account and not connected_account.auth_token:
            # Account exists but token was cleared (logout), mark as disconnected
            is_connected = False
        
        platform_data = {
            'platform_id': platform.platform_id,
            'platform_name': platform.platform_name,
            'is_connected': is_connected,
            'username': connected_account.username_on_platform if connected_account else None,
            'account_id': connected_account.account_id if connected_account else None
        }
        platforms.append(platform_data)
    
    return render_template('connect_platform.html', platforms=platforms, user_accounts=user_accounts)

@app.route('/spotify_callback')
@login_required
def spotify_callback():
    """Handle Spotify OAuth callback"""
    try:
        code = request.args.get('code')
        if not code:
            flash('Spotify authorization failed')
            return redirect(url_for('dashboard'))
        
        # Exchange code for access token
        print(f"Spotify OAuth config - Client ID: {SPOTIFY_CLIENT_ID[:10]}...")
        print(f"Spotify OAuth config - Redirect URI: {SPOTIFY_REDIRECT_URI}")
        
        spotify_oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope='playlist-read-private playlist-read-collaborative user-read-private playlist-modify-public playlist-modify-private'
        )
        
        token_info = spotify_oauth.get_access_token(code)
        access_token = token_info['access_token']
        print(f"Spotify access token obtained: {access_token[:20]}...")
        
        # Get user info from Spotify with error handling
        sp = spotipy.Spotify(auth=access_token)
        try:
            user_info = sp.current_user()
            print(f"Spotify callback - user info: {user_info}")
        except Exception as e:
            print(f"Spotify callback error: {e}")
            if hasattr(e, 'http_status') and e.http_status == 403:
                flash('Spotify connection failed: Your account may not be registered or the app needs proper configuration. Please check your Spotify Developer Dashboard settings.', 'error')
            else:
                flash('Spotify connection failed: Unable to get user information. Please try again.', 'error')
            return redirect(url_for('dashboard'))
        
        # Get or create platform
        platform = Platform.query.filter_by(platform_name='Spotify').first()
        if not platform:
            platform = Platform(platform_name='Spotify', api_details='{"api_url": "https://api.spotify.com"}')
            db.session.add(platform)
            db.session.commit()
        
        # Get Spotify username
        spotify_username = user_info.get('display_name') or user_info.get('id', f"user_{current_user.user_id}")
        
        # Check if user already has a Spotify account
        existing_account = UserPlatformAccount.query.filter_by(
            user_id=current_user.user_id,
            platform_id=platform.platform_id
        ).first()
        
        if existing_account:
            # Update existing account
            existing_account.auth_token = access_token
            existing_account.username_on_platform = spotify_username
            flash('Spotify account updated successfully')
        else:
            # Create new account
            user_account = UserPlatformAccount(
                user_id=current_user.user_id,
                platform_id=platform.platform_id,
                username_on_platform=spotify_username,
                auth_token=access_token
            )
            db.session.add(user_account)
            flash('Spotify connected successfully')
        
        db.session.commit()
        
        # Fetch playlists
        fetch_spotify_playlists(current_user.user_id, access_token)
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        flash(f'Error connecting Spotify: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/youtube_callback')
@login_required
def youtube_callback():
    """Handle YouTube OAuth callback"""
    try:
        code = request.args.get('code')
        if not code:
            flash('YouTube authorization failed')
            return redirect(url_for('dashboard'))
        
        # Exchange code for access token
        import requests
        
        token_data = {
            'client_id': YOUTUBE_CLIENT_ID,
            'client_secret': YOUTUBE_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': YOUTUBE_REDIRECT_URI
        }
        
        token_response = requests.post('https://oauth2.googleapis.com/token', data=token_data)
        token_json = token_response.json()
        
        if 'access_token' not in token_json:
            flash('Failed to get YouTube access token')
            return redirect(url_for('dashboard'))
        
        access_token = token_json['access_token']
        
        # Get YouTube channel info
        headers = {'Authorization': f'Bearer {access_token}'}
        channel_response = requests.get(
            'https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',
            headers=headers
        )
        
        print(f"YouTube channel response status: {channel_response.status_code}")
        if channel_response.status_code != 200:
            print(f"YouTube channel error: {channel_response.text}")
            flash('YouTube connection failed: Unable to get channel information. Please try again.', 'error')
            return redirect(url_for('dashboard'))
        
        if channel_response.status_code == 200:
            channel_data = channel_response.json()
            if channel_data.get('items'):
                channel_info = channel_data['items'][0]['snippet']
                # Use the actual channel title as username, fallback to customUrl, then ID
                youtube_username = (channel_info.get('title') or 
                                 channel_info.get('customUrl') or 
                                 f"user_{current_user.user_id}")
                
                # Get the Gmail account ID for conflict checking
                gmail_account_id = channel_data['items'][0]['id']
            else:
                youtube_username = f"user_{current_user.user_id}"
                gmail_account_id = None
        else:
            youtube_username = f"user_{current_user.user_id}"
            gmail_account_id = None
        
        # Use separate transactions to avoid locks
        try:
            # First transaction: Ensure platform exists
            platform = Platform.query.filter_by(platform_name='YouTube').first()
            if not platform:
                platform = Platform(platform_name='YouTube', api_details='{"api_url": "https://www.youtube.com", "version": "v3"}')
                db.session.add(platform)
                db.session.commit()
                # Reload platform to get fresh object
                platform = Platform.query.filter_by(platform_name='YouTube').first()
            
            # Second transaction: Handle account creation/update
            # Start fresh session to avoid conflicts
            db.session.expunge_all()  # Clear session cache
            
            # Check if this Gmail account is already connected by another user
            if gmail_account_id:
                conflicting_account = UserPlatformAccount.query.join(User).filter(
                    UserPlatformAccount.platform_id == platform.platform_id,
                    UserPlatformAccount.username_on_platform == gmail_account_id,
                    User.user_id != current_user.user_id
                ).first()
                
                if conflicting_account:
                    flash(f'This Gmail account is already connected to another Sync Tunes account. Please use a different Gmail account or contact support.')
                    return redirect(url_for('connect_platform'))
            
            existing_account = UserPlatformAccount.query.filter_by(
                user_id=current_user.user_id,
                platform_id=platform.platform_id
            ).first()
            
            if existing_account:
                # Update existing account in a single operation
                UserPlatformAccount.query.filter_by(
                    user_id=current_user.user_id,
                    platform_id=platform.platform_id
                ).update({
                    'auth_token': access_token,
                    'username_on_platform': youtube_username
                })
                flash('YouTube account updated successfully')
            else:
                # Create new account
                user_account = UserPlatformAccount(
                    user_id=current_user.user_id,
                    platform_id=platform.platform_id,
                    username_on_platform=gmail_account_id or youtube_username,
                    auth_token=access_token
                )
                db.session.add(user_account)
                flash('YouTube connected successfully')
            
            # Commit the account changes
            db.session.commit()
            
        except Exception as db_error:
            db.session.rollback()
            raise db_error
        
        # Fetch playlists in a separate operation to avoid lock conflicts
        try:
            fetch_youtube_playlists(current_user.user_id, access_token)
        except Exception as playlist_error:
            print(f"Warning: Could not fetch playlists immediately: {playlist_error}")
            flash('YouTube connected successfully! You can refresh playlists from the dashboard.')
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        db.session.rollback()  # Roll back any uncommitted changes
        flash(f'Error connecting YouTube: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/logs')
@login_required
def logs():
    """View sync logs"""
    try:
        # Get sync logs - admins see all logs, users see only their own
        if hasattr(current_user, 'admin_id'):
            # Admin can see all sync logs
            sync_logs = SyncLog.query.order_by(SyncLog.timestamp.desc()).all()
        else:
            # Regular user sees only their own logs
            sync_logs = SyncLog.query.filter_by(user_id=current_user.user_id).order_by(SyncLog.timestamp.desc()).all()
        
        # Get additional data for each log
        for log in sync_logs:
            source_account = db.session.get(UserPlatformAccount, log.source_account_id)
            if source_account:
                source_account.platform = db.session.get(Platform, source_account.platform_id)
                log.source_account = source_account
            
            dest_account = db.session.get(UserPlatformAccount, log.destination_account_id)
            if dest_account:
                dest_account.platform = db.session.get(Platform, dest_account.platform_id)
                log.destination_account = dest_account
            
            log.playlist = db.session.get(Playlist, log.playlist_id)
            log.user = db.session.get(User, log.user_id)
        
        # Get statistics
        total_logs = len(sync_logs)
        total_songs_synced = sum(log.songs_added or 0 for log in sync_logs)
        
        stats = {
            'total_logs': total_logs,
            'total_songs_synced': total_songs_synced,
            'avg_songs_per_sync': total_songs_synced / total_logs if total_logs > 0 else 0
        }
        
        return render_template('logs.html', sync_logs=sync_logs, stats=stats)
        
    except Exception as e:
        flash(f'Error loading logs: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/profile')
@login_required
def profile():
    """User profile page"""
    # Check if current user is admin - redirect to admin dashboard
    if hasattr(current_user, 'admin_id'):
        return redirect(url_for('admin_dashboard'))
    user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).filter(UserPlatformAccount.auth_token.isnot(None)).all()
    
    # Add platform info to accounts
    for account in user_accounts:
        account.platform = db.session.get(Platform, account.platform_id)
    
    # Create platforms data structure that the template expects
    all_platforms = Platform.query.all()
    platforms = []
    
    for platform in all_platforms:
        # Check if user has this platform connected
        user_account = next((acc for acc in user_accounts if acc.platform_id == platform.platform_id), None)
        
        platform_data = {
            'name': platform.platform_name,
            'connected': user_account is not None and user_account.auth_token is not None,
            'username': user_account.username_on_platform if user_account else None,
            'account_id': user_account.account_id if user_account else None
        }
        platforms.append(platform_data)
    
    return render_template('profile.html', user=current_user, user_accounts=user_accounts, platforms=platforms)

@app.route('/disconnect_platform/<int:account_id>')
@login_required
def disconnect_platform(account_id):
    """Disconnect a platform account"""
    try:
        account = UserPlatformAccount.query.get_or_404(account_id)
        
        # Verify ownership
        if account.user_id != current_user.user_id:
            flash('Access denied')
            return redirect(url_for('profile'))
        
        # Get platform name for message
        platform = db.session.get(Platform, account.platform_id)
        platform_name = platform.platform_name if platform else 'Unknown'
        
        # Delete associated playlists and their relationships
        playlists = Playlist.query.filter_by(account_id=account_id).all()
        for playlist in playlists:
            PlaylistSong.query.filter_by(playlist_id=playlist.playlist_id).delete()
            db.session.delete(playlist)
        
        # Delete the account
        db.session.delete(account)
        db.session.commit()
        
        flash(f'{platform_name} account disconnected successfully')
        
    except Exception as e:
        flash(f'Error disconnecting platform: {str(e)}')
        db.session.rollback()
    
    return redirect(url_for('profile'))

@app.route('/refresh_playlists')
@login_required
def refresh_playlists():
    """Refresh playlists from all connected platforms"""
    try:
        user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).all()
        
        for account in user_accounts:
            platform = db.session.get(Platform, account.platform_id)
            
            if platform.platform_name == 'Spotify' and account.auth_token:
                fetch_spotify_playlists(current_user.user_id, account.auth_token)
            elif platform.platform_name == 'YouTube' and account.auth_token:
                fetch_youtube_playlists(current_user.user_id, account.auth_token)
        
        flash('Playlists refreshed successfully')
        
    except Exception as e:
        flash(f'Error refreshing playlists: {str(e)}')
    
    return redirect(url_for('dashboard'))

@app.route('/playlist_details/<int:playlist_id>')
@login_required
def playlist_details(playlist_id):
    """View playlist details"""
    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        
        # Verify ownership
        account = db.session.get(UserPlatformAccount, playlist.account_id)
        if account.user_id != current_user.user_id:
            flash('Access denied')
            return redirect(url_for('dashboard'))
        
        # Get playlist songs
        playlist_songs = PlaylistSong.query.filter_by(playlist_id=playlist.playlist_id).all()
        
        songs = []
        for ps in playlist_songs:
            song = db.session.get(Song, ps.song_id)
            if song:
                songs.append({
                    'song_id': song.song_id,
                    'title': song.title,
                    'artist': song.artist,
                    'album': song.album,
                    'duration': song.duration,
                    'added_at': ps.added_at
                })
        
        # Get platform info
        platform = db.session.get(Platform, account.platform_id)
        
        # Get other playlists for syncing
        other_playlists = []
        user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).all()
        for user_account in user_accounts:
            account_playlists = Playlist.query.filter_by(account_id=user_account.account_id).all()
            for other_playlist in account_playlists:
                if other_playlist.playlist_id != playlist.playlist_id:
                    other_playlist.platform = db.session.get(Platform, user_account.platform_id)
                    other_playlists.append(other_playlist)
        
        return render_template('playlist_details.html', 
                             playlist=playlist, 
                             songs=songs, 
                             platform=platform,
                             other_playlists=other_playlists)
        
    except Exception as e:
        flash(f'Error loading playlist details: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/debug_logs')
@login_required
def debug_logs():
    """View debug logs for troubleshooting"""
    try:
        with open('/tmp/sync_debug.log', 'r') as f:
            logs = f.read()
        return f"""
        <html>
        <head>
            <title>Debug Logs</title>
            <meta http-equiv="refresh" content="5">
            <style>
                body {{ font-family: monospace; background: #1a1a1a; color: #00ff00; }}
                pre {{ white-space: pre-wrap; word-wrap: break-word; }}
                .refresh {{ color: #ffff00; }}
            </style>
        </head>
        <body>
            <div class="refresh">Auto-refreshing every 5 seconds...</div>
            <pre>{logs}</pre>
        </body>
        </html>
        """
    except FileNotFoundError:
        return "No debug logs found yet. Try syncing first."
    except Exception as e:
        return f"Error reading logs: {str(e)}"

@app.route('/test_debug')
@login_required
def test_debug():
    """Test debug logging"""
    try:
        # Test file logging
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"=== TEST DEBUG {datetime.now()} ===\n")
            f.write(f"User: {current_user.email if hasattr(current_user, 'email') else 'Unknown'}\n")
            f.write(f"Test successful!\n")
        
        return f"Debug test successful! Check /debug_logs to see the log entry."
    except Exception as e:
        return f"Debug test failed: {str(e)}"

@app.route('/cleanup_logs')
@login_required
def cleanup_logs():
    """Clean up old sync logs"""
    try:
        # Delete logs older than 30 days
        cutoff_date = datetime.now().date() - timedelta(days=30)
        
        if hasattr(current_user, 'admin_id'):
            # Admin can clean all logs
            old_logs = SyncLog.query.filter(SyncLog.timestamp < cutoff_date).all()
        else:
            # Users can only clean their own logs
            old_logs = SyncLog.query.filter(
                SyncLog.user_id == current_user.user_id,
                SyncLog.timestamp < cutoff_date
            ).all()
        
        count = len(old_logs)
        
        for log in old_logs:
            db.session.delete(log)
        
        db.session.commit()
        flash(f'Cleaned up {count} old log entries')
        
    except Exception as e:
        flash(f'Error cleaning up logs: {str(e)}')
        db.session.rollback()
    
    return redirect(url_for('logs'))

@app.route('/sync_playlist_songs', methods=['POST'])
@login_required
def sync_playlist_songs():
    """Sync selected songs from one playlist to another"""
    print("=== SYNC_PLAYLIST_SONGS CALLED ===")
    print(f"Source playlist ID: {request.form.get('source_playlist_id')}")
    print(f"Target playlist ID: {request.form.get('target_playlist_id')}")
    print(f"Song IDs: {request.form.getlist('song_ids')}")
    
    # Immediate file logging
    try:
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"=== SYNC_PLAYLIST_SONGS CALLED {datetime.now()} ===\n")
            f.write(f"Source playlist ID: {request.form.get('source_playlist_id')}\n")
            f.write(f"Target playlist ID: {request.form.get('target_playlist_id')}\n")
            f.write(f"Song IDs: {request.form.getlist('song_ids')}\n")
    except Exception as e:
        print(f"File logging error: {e}")
    
    try:
        source_playlist_id = request.form.get('source_playlist_id')
        target_playlist_id = request.form.get('target_playlist_id')
        song_ids = request.form.getlist('song_ids')
        
        # Log validation step
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"Validation step - Source: {source_playlist_id}, Target: {target_playlist_id}, Songs: {song_ids}\n")
        
        if not source_playlist_id or not target_playlist_id or not song_ids:
            with open('/tmp/sync_debug.log', 'a') as f:
                f.write("ERROR: Missing required parameters\n")
            flash('Please select source playlist, target playlist, and songs to sync.')
            return redirect(url_for('dashboard'))
        
        # Log validation passed
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write("Validation passed - proceeding with sync\n")
        
        # Verify ownership of both playlists
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write("Fetching playlists from database\n")
        
        source_playlist = Playlist.query.get_or_404(source_playlist_id)
        target_playlist = Playlist.query.get_or_404(target_playlist_id)
        
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"Source playlist: {source_playlist.name}, Target playlist: {target_playlist.name}\n")
        
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write("Looking up user account\n")
        
        user_account = UserPlatformAccount.query.filter_by(
            user_id=current_user.user_id,
            account_id=source_playlist.account_id
        ).first()
        
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"User account found: {user_account is not None}\n")
            if user_account:
                f.write(f"Source account ID: {source_playlist.account_id}, Target account ID: {target_playlist.account_id}\n")
                f.write(f"User account ID: {user_account.account_id}\n")
        
        if not user_account:
            with open('/tmp/sync_debug.log', 'a') as f:
                f.write("ERROR: No user account found for source playlist\n")
            flash('You do not have access to the source playlist.')
            return redirect(url_for('dashboard'))
        
        # For cross-platform syncing, we need to get the target platform account
        target_user_account = UserPlatformAccount.query.filter_by(
            user_id=current_user.user_id,
            account_id=target_playlist.account_id
        ).first()
        
        
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"Target user account found: {target_user_account is not None}\n")
        
        if not target_user_account:
            with open('/tmp/sync_debug.log', 'a') as f:
                f.write("ERROR: No user account found for target playlist\n")
            flash('You do not have access to the target playlist.')
            return redirect(url_for('dashboard'))
        
        # Get platform info for the target platform
        platform = db.session.get(Platform, target_user_account.platform_id)
        
        # Get source playlist platform info
        source_platform = db.session.get(Platform, user_account.platform_id)
        
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"Target platform: {platform.platform_name if platform else 'None'}\n")
            f.write(f"Source platform: {source_platform.platform_name if source_platform else 'None'}\n")
        
        # Sync songs to database first
        songs_added = 0
        songs_skipped = 0
        songs_to_add_to_platform = []
        synced_song_ids = []  # Track which songs were actually synced
        
        for song_id in song_ids:
            song = db.session.get(Song, song_id)
            if song:
                # Check if song already exists in target playlist
                existing = PlaylistSong.query.filter_by(
                    playlist_id=target_playlist.playlist_id,
                    song_id=song.song_id
                ).first()
                
                if not existing:
                    # Add to database
                    playlist_song = PlaylistSong(
                        playlist_id=target_playlist.playlist_id,
                        song_id=song.song_id,
                        added_at=datetime.now().date()
                    )
                    db.session.add(playlist_song)
                    songs_added += 1
                    synced_song_ids.append(song.song_id)  # Track this synced song
                    
                    # Prepare for platform API call
                    # If syncing from YouTube to another platform, use Gemini for better parsing
                    if source_platform.platform_name == 'YouTube' and platform.platform_name != 'YouTube':
                        # Get the original YouTube title from the platform song mapping
                        platform_song = PlatformSong.query.filter_by(
                            song_id=song.song_id,
                            platform_id=source_platform.platform_id
                        ).first()
                        
                        if platform_song:
                            # Extract the original YouTube title from the album field
                            original_title = song.album
                            if original_title.startswith("YouTube_ORIGINAL:"):
                                original_title = original_title.replace("YouTube_ORIGINAL:", "")
                                print(f"Found original YouTube title: '{original_title}'")
                                
                                # Step 1: Try YouTube Music search first (most accurate)
                                ytmusic_song_name, ytmusic_artist_name, ytmusic_album_name, ytmusic_confidence = search_youtube_music_for_metadata(
                                    original_title, song.artist
                                )
                                
                                if ytmusic_song_name and ytmusic_confidence >= 0.7:
                                    # Use YouTube Music results (high confidence)
                                    print(f"YouTube Music result: '{ytmusic_song_name}' by '{ytmusic_artist_name}' from '{ytmusic_album_name}' (confidence: {ytmusic_confidence:.2f})")
                                    
                                    songs_to_add_to_platform.append({
                                        'title': ytmusic_song_name,
                                        'artist': ytmusic_artist_name,
                                        'album': ytmusic_album_name,
                                        'original_title': original_title,
                                        'duration': song.duration,
                                        'gemini_confidence': ytmusic_confidence,
                                        'channel_name': song.artist,
                                        'source': 'youtube_music'
                                    })
                                else:
                                    # Step 2: Try YouTube description analysis
                                    video_id = platform_song.platform_specific_id
                                    desc_song_name, desc_artist_name, desc_album_name, desc_confidence = analyze_youtube_description(
                                        video_id, original_title, song.artist
                                    )
                                    
                                    if desc_song_name and desc_confidence >= 0.7:
                                        # Use description analysis results (high confidence)
                                        print(f"YouTube description analysis result: '{desc_song_name}' by '{desc_artist_name}' from '{desc_album_name}' (confidence: {desc_confidence:.2f})")
                                        
                                        songs_to_add_to_platform.append({
                                            'title': desc_song_name,
                                            'artist': desc_artist_name,
                                            'album': desc_album_name,
                                            'original_title': original_title,
                                            'duration': song.duration,
                                            'gemini_confidence': desc_confidence,
                                            'channel_name': song.artist,
                                            'source': 'youtube_description'
                                        })
                                    else:
                                        # Step 3: Try Gemini with YouTube URL (FINAL FALLBACK)
                                        print(f"YouTube Music and description analysis failed, trying Gemini with YouTube URL...")
                                        
                                        url_song_name, url_artist_name, url_album_name, url_confidence = get_spotify_song_name_from_youtube_url(
                                            video_id, original_title, song.artist
                                        )
                                        
                                        if url_song_name and url_confidence >= 0.6:
                                            # Use URL analysis results (good confidence)
                                            print(f"Gemini URL analysis result: '{url_song_name}' by '{url_artist_name}' from '{url_album_name}' (confidence: {url_confidence:.2f})")
                                            
                                            songs_to_add_to_platform.append({
                                                'title': url_song_name,
                                                'artist': url_artist_name,
                                                'album': url_album_name,
                                                'original_title': original_title,
                                                'duration': song.duration,
                                                'gemini_confidence': url_confidence,
                                                'channel_name': song.artist,
                                                'source': 'youtube_url_analysis'
                                            })
                                        else:
                                            # Step 4: Fallback to title parsing
                                            print(f"All advanced methods failed, trying basic title parsing...")
                                            
                                            # Extract clean song name using Gemini
                                            parsed_title, _ = parse_youtube_title_for_sync(original_title, song.artist)
                                            
                                            # Get artist and album info using Gemini with confidence
                                            artist_name, album_name, gemini_confidence = get_artist_and_album_info(parsed_title, original_title, song.artist)
                                            
                                            print(f"Gemini title parsing result: '{parsed_title}' by '{artist_name}' from '{album_name}' (confidence: {gemini_confidence:.2f})")
                                            
                                            songs_to_add_to_platform.append({
                                                'title': parsed_title,
                                                'artist': artist_name,
                                                'album': album_name,
                                                'original_title': original_title,
                                                'duration': song.duration,
                                                'gemini_confidence': gemini_confidence,
                                                'channel_name': song.artist,
                                                'source': 'title_parsing'
                                            })
                            else:
                                # Fallback to stored song data
                                print(f"No original title found, using stored data: '{song.title}' by '{song.artist}'")
                                songs_to_add_to_platform.append({
                                    'title': song.title,
                                    'artist': song.artist,
                                    'album': song.album,
                                    'duration': song.duration
                                })
                        else:
                            # Fallback to original song data
                            songs_to_add_to_platform.append({
                                'title': song.title,
                                'artist': song.artist,
                                'album': song.album,
                                'duration': song.duration
                            })
                    else:
                        # For other sync types, use original song data
                        songs_to_add_to_platform.append({
                        'title': song.title,
                        'artist': song.artist,
                        'album': song.album,
                        'duration': song.duration
                    })
                else:
                    songs_skipped += 1
        
        # Commit database changes
        db.session.commit()
        
        # Try to update the real platform playlist
        platform_songs_added = 0
        # Log to file for better debugging
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"=== SYNC DEBUG START ===\n")
            f.write(f"Sync debug - Platform: {platform.platform_name if platform else 'None'}\n")
            f.write(f"Sync debug - Target account token: {'Present' if target_user_account.auth_token else 'Missing'}\n")
            f.write(f"Songs to add to platform: {len(songs_to_add_to_platform)}\n")
            f.write(f"Target playlist: {target_playlist.name if target_playlist else 'None'}\n")
            f.write(f"Target playlist platform ID: {target_playlist.platform_playlist_id if target_playlist else 'None'}\n")
        
        print(f"=== SYNC DEBUG START ===")
        print(f"Sync debug - Platform: {platform.platform_name if platform else 'None'}")
        print(f"Sync debug - User account token: {'Present' if user_account.auth_token else 'Missing'}")
        print(f"Songs to add to platform: {len(songs_to_add_to_platform)}")
        print(f"Target playlist: {target_playlist.name if target_playlist else 'None'}")
        print(f"Target playlist platform ID: {target_playlist.platform_playlist_id if target_playlist else 'None'}")
        
        if platform and target_user_account.auth_token and songs_to_add_to_platform:
            if platform.platform_name == 'YouTube':
                print("=== CALLING update_youtube_playlist ===")
                platform_songs_added = update_youtube_playlist(
                    target_user_account.auth_token, 
                    target_playlist, 
                    songs_to_add_to_platform
                )
                print(f"YouTube sync result: {platform_songs_added} songs added")
            elif platform.platform_name == 'Spotify':
                print("=== CALLING update_spotify_playlist ===")
                platform_songs_added = update_spotify_playlist(
                    target_user_account.auth_token, 
                    target_playlist, 
                    songs_to_add_to_platform
                )
                print(f"Spotify sync result: {platform_songs_added} songs added")
        else:
            print("=== SYNC CONDITIONS NOT MET ===")
            print(f"Platform exists: {platform is not None}")
            print(f"Target account token exists: {target_user_account.auth_token is not None}")
            print(f"Songs to add exists: {len(songs_to_add_to_platform) > 0}")
        print(f"=== SYNC DEBUG END ===")
        
        # Create sync log - record the TARGET playlist where songs were added
        sync_log = SyncLog(
            user_id=current_user.user_id,
            source_account_id=user_account.account_id,
            destination_account_id=user_account.account_id,
            playlist_id=target_playlist.playlist_id,  # Changed to target playlist
            total_songs_synced=songs_added,
            songs_added=songs_added,
            songs_removed=0,
            timestamp=datetime.now().date()
        )
        db.session.add(sync_log)
        db.session.commit()
        
        # Store the sync log ID for reference
        sync_log_id = sync_log.sync_id
        
        # Record exactly which songs were synced
        for song_id in synced_song_ids:
            sync_song = SyncSong(
                sync_id=sync_log_id,
                song_id=song_id,
                action='added',
                timestamp=datetime.now()
            )
            db.session.add(sync_song)
        
        db.session.commit()
        
        # User feedback
        # Check if there are pending tracks for user confirmation
        pending_tracks = session.get('pending_tracks', [])
        print(f"=== SYNC DEBUG END ===")
        print(f"Pending tracks in session: {len(pending_tracks)}")
        
        if songs_added > 0:
            if platform_songs_added > 0:
                flash(f'Successfully synced {songs_added} songs! {platform_songs_added} songs added to {platform.platform_name} playlist.')
            else:
                flash(f'Successfully synced {songs_added} songs! All songs were automatically added to {platform.platform_name} playlist.')
        elif songs_skipped > 0:
            flash(f'No new songs to sync - all {songs_skipped} selected songs already exist in the target playlist.')
        else:
            flash('No songs were selected for syncing.')
        
        # If there are pending tracks (songs not found), redirect to confirmation page
        if pending_tracks:
            print(f"Redirecting to confirmation page with {len(pending_tracks)} pending tracks")
            flash(f'Found {len(pending_tracks)} songs that could not be found on Spotify. Please review and select alternative tracks.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        return redirect(url_for('playlist_details', playlist_id=source_playlist_id))
        
    except Exception as e:
        flash(f'Error syncing songs: {str(e)}')
        db.session.rollback()
        return redirect(url_for('dashboard'))

@app.route('/confirm_fallback_tracks')
@login_required
def confirm_fallback_tracks():
    """Show fallback tracks for user confirmation"""
    try:
        pending_tracks = session.get('pending_tracks', [])
        print(f"DEBUG: Pending tracks count: {len(pending_tracks)}")
        print(f"DEBUG: Pending tracks data: {pending_tracks}")
        
        if not pending_tracks:
            flash('No pending tracks to confirm.')
            return redirect(url_for('dashboard'))
        
        # Validate data structure (handle both old and new formats)
        for i, track_data in enumerate(pending_tracks):
            print(f"DEBUG: Track {i}: {track_data}")
            # Check for new data structure
            if 'song_info' not in track_data and 'original_song' not in track_data:
                print(f"ERROR: Track {i} missing both 'song_info' and 'original_song' keys")
                flash('Error: Invalid track data structure.')
                return redirect(url_for('dashboard'))
        
        return render_template('confirm_fallback_tracks.html', pending_tracks=pending_tracks)
        
    except Exception as e:
        print(f"ERROR in confirm_fallback_tracks: {e}")
        flash(f'Error loading fallback tracks: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/confirm_track', methods=['POST'])
@login_required
def confirm_track():
    """Confirm a fallback track selection"""
    try:
        track_index = int(request.form.get('track_index'))
        
        pending_tracks = session.get('pending_tracks', [])
        if track_index >= len(pending_tracks):
            flash('Invalid track selection.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        track_data = pending_tracks[track_index]
        if not track_data['spotify_track']:
            flash('No track to add.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        selected_track = track_data['spotify_track']
        song_info = track_data['song_info']
        
        # Get playlist ID from the song info or use a default
        playlist_id = song_info.get('playlist_id')
        
        # Add the selected track to Spotify playlist
        try:
            # Get user's Spotify account
            platform = Platform.query.filter_by(platform_name='Spotify').first()
            if not platform:
                flash('Spotify platform not found.')
                return redirect(url_for('confirm_fallback_tracks'))
            
            user_account = UserPlatformAccount.query.filter_by(
                user_id=current_user.user_id,
                platform_id=platform.platform_id
            ).first()
            
            if not user_account or not user_account.auth_token:
                flash('Spotify account not connected.')
                return redirect(url_for('confirm_fallback_tracks'))
            
            # Add track to playlist
            sp = spotipy.Spotify(auth=user_account.auth_token)
            sp.playlist_add_items(playlist_id, [selected_track['uri']])
            
            # Remove this track from pending tracks
            pending_tracks.pop(track_index)
            session['pending_tracks'] = pending_tracks
            session.modified = True
            
            # Learning mechanism: Track exact match confirmations
            if selected_track.get('is_exact_match'):
                exact_match_count = session.get('exact_match_confirmations', 0) + 1
                session['exact_match_confirmations'] = exact_match_count
                session.modified = True
                
                # Auto-enable after 5 exact match confirmations
                if exact_match_count >= 5 and not session.get('auto_confirm_exact_matches'):
                    session['auto_confirm_exact_matches'] = True
                    session.modified = True
                    flash(f"Successfully added '{selected_track['name']}' by {selected_track['artist']} to playlist! 🎉 Auto-confirm enabled for exact matches after {exact_match_count} confirmations.")
                else:
                    flash(f"Successfully added '{selected_track['name']}' by {selected_track['artist']} to playlist!")
            else:
                flash(f"Successfully added '{selected_track['name']}' by {selected_track['artist']} to playlist!")
            
            # Log success
            with open('/tmp/sync_debug.log', 'a') as f:
                f.write(f"User confirmed track: '{selected_track['name']}' by {selected_track['artist']} - URI: {selected_track['uri']}\n")
            
        except Exception as e:
            flash(f'Error adding track to playlist: {str(e)}')
            with open('/tmp/sync_debug.log', 'a') as f:
                f.write(f"Error adding confirmed track: {str(e)}\n")
        
        # If no more pending tracks, redirect to dashboard
        if not pending_tracks:
            return redirect(url_for('dashboard'))
        else:
            return redirect(url_for('confirm_fallback_tracks'))
            
    except Exception as e:
        flash(f'Error processing track confirmation: {str(e)}')
        return redirect(url_for('confirm_fallback_tracks'))

@app.route('/skip_track', methods=['POST'])
@login_required
def skip_track():
    """Skip a fallback track (don't add to playlist)"""
    try:
        track_index = int(request.form.get('track_index'))
        
        pending_tracks = session.get('pending_tracks', [])
        if track_index >= len(pending_tracks):
            flash('Invalid track selection.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        # Remove this track from pending tracks
        pending_tracks.pop(track_index)
        session['pending_tracks'] = pending_tracks
        session.modified = True
        
        flash('Track skipped.')
        
        # Log skip
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"User skipped track: {pending_tracks[track_index]['song_info']['title']}\n")
        
        # If no more pending tracks, redirect to dashboard
        if not pending_tracks:
            return redirect(url_for('dashboard'))
        else:
            return redirect(url_for('confirm_fallback_tracks'))
            
    except Exception as e:
        flash(f'Error skipping track: {str(e)}')
        return redirect(url_for('confirm_fallback_tracks'))

@app.route('/toggle_auto_confirm', methods=['POST'])
@login_required
def toggle_auto_confirm():
    """Toggle auto-confirm for exact matches"""
    try:
        auto_confirm = request.form.get('auto_confirm') == 'true'
        session['auto_confirm_exact_matches'] = auto_confirm
        session.modified = True
        
        if auto_confirm:
            flash('Auto-confirm enabled: Exact matches will be added automatically without confirmation.')
        else:
            flash('Auto-confirm disabled: All tracks will require user confirmation.')
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        flash(f'Error updating auto-confirm setting: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/sync_cross_platform', methods=['POST'])
@login_required
def sync_cross_platform():
    """Sync entire playlist from one platform to another (e.g., YouTube to Spotify)"""
    try:
        source_playlist_id = request.form.get('source_playlist_id')
        target_playlist_id = request.form.get('target_playlist_id')
        
        if not source_playlist_id or not target_playlist_id:
            flash('Please select both source and target playlists.')
            return redirect(url_for('dashboard'))
        
        # Get playlists
        source_playlist = Playlist.query.get_or_404(source_playlist_id)
        target_playlist = Playlist.query.get_or_404(target_playlist_id)
        
        # Verify ownership
        source_account = UserPlatformAccount.query.filter_by(
            user_id=current_user.user_id,
            account_id=source_playlist.account_id
        ).first()
        
        target_account = UserPlatformAccount.query.filter_by(
            user_id=current_user.user_id,
            account_id=target_playlist.account_id
        ).first()
        
        if not source_account or not target_account:
            flash('Access denied - you must own both playlists.')
            return redirect(url_for('dashboard'))
        
        # Get platform names
        source_platform = db.session.get(Platform, source_account.platform_id)
        target_platform = db.session.get(Platform, target_account.platform_id)
        
        # Get all user accounts for cross-platform sync
        user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).all()
        
        # Perform cross-platform sync
        success, message = sync_playlist_cross_platform(
            source_playlist, 
            target_playlist, 
            source_platform.platform_name, 
            target_platform.platform_name, 
            user_accounts
        )
        
        if success:
            flash(f'Cross-platform sync successful: {message}')
        else:
            flash(f'Cross-platform sync failed: {message}')
        
        return redirect(url_for('playlist_details', playlist_id=source_playlist_id))
        
    except Exception as e:
        flash(f'Error in cross-platform sync: {str(e)}')
        db.session.rollback()
        return redirect(url_for('dashboard'))

@app.route('/sync_details/<int:sync_id>')
@login_required
def sync_details(sync_id):
    """Get detailed information about a sync operation"""
    try:
        sync_log = SyncLog.query.get_or_404(sync_id)
        
        # Verify ownership - admins can see all, users only their own
        if not hasattr(current_user, 'admin_id') and sync_log.user_id != current_user.user_id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get related data
        source_account = db.session.get(UserPlatformAccount, sync_log.source_account_id)
        destination_account = db.session.get(UserPlatformAccount, sync_log.destination_account_id)
        
        source_platform = db.session.get(Platform, source_account.platform_id) if source_account else None
        destination_platform = db.session.get(Platform, destination_account.platform_id) if destination_account else None
        
        playlist = db.session.get(Playlist, sync_log.playlist_id)
        user = db.session.get(User, sync_log.user_id)
        
        # Get the exact songs that were synced using the new SyncSong table
        synced_songs = []
        
        # Query the SyncSong table to get the exact songs synced in this operation
        sync_song_records = SyncSong.query.filter_by(sync_id=sync_log.sync_id).all()
        
        for sync_song in sync_song_records:
            song = db.session.get(Song, sync_song.song_id)
            if song:
                synced_songs.append({
                    'song_id': song.song_id,
                    'title': song.title,
                    'artist': song.artist,
                    'album': song.album,
                    'duration': song.duration,
                    'action': sync_song.action,
                    'note': f'{sync_song.action.title()} on {sync_song.timestamp.strftime("%Y-%m-%d %H:%M")}'
                })
        
        sync_data = {
            'sync_id': sync_log.sync_id,
            'user_name': user.name if user else 'Unknown',
            'source_platform': source_platform.platform_name if source_platform else 'Unknown',
            'destination_platform': destination_platform.platform_name if destination_platform else 'Unknown',
            'source_username': source_account.username_on_platform if source_account else 'Unknown',
            'destination_username': destination_account.username_on_platform if destination_account else 'Unknown',
            'playlist_name': playlist.name if playlist else 'Unknown',
            'total_songs_synced': sync_log.total_songs_synced,
            'songs_added': sync_log.songs_added,
            'songs_removed': sync_log.songs_removed,
            'timestamp': sync_log.timestamp.strftime('%Y-%m-%d'),
            'synced_songs': synced_songs
        }
        
        return jsonify({
            'success': True,
            'sync_data': sync_data
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/logout')
@login_required
def logout():
    """Logout user and clear platform connections"""
    try:
        # Clear platform connections for regular users (not admins)
        if not hasattr(current_user, 'admin_id'):
            user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).all()
            for account in user_accounts:
                # Clear the auth token to force re-authentication
                account.auth_token = None
            db.session.commit()
            flash('Logged out successfully. Platform connections cleared for security.')
        else:
            flash('Admin logged out successfully.')
    except Exception as e:
        print(f"Error clearing platform connections: {e}")
        flash('Logged out successfully.')
    
    logout_user()
    return redirect(url_for('index'))

@app.route('/init_db')
def init_db():
    """Initialize database tables"""
    db.create_all()
    return 'Database initialized!'

@app.route('/update_db')
def update_db():
    """Update database with new tables"""
    try:
        db.create_all()
        return 'Database updated with new tables!'
    except Exception as e:
        return f'Error updating database: {str(e)}'

if __name__ == '__main__':
    # Ensure SQLAlchemy uses modern syntax
    from sqlalchemy import text
    
    with app.app_context():
        # Only apply SQLite optimizations if using SQLite
        if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
            try:
                db.engine.execute(text("PRAGMA journal_mode=WAL;"))
                db.engine.execute(text("PRAGMA synchronous=NORMAL;"))
                db.engine.execute(text("PRAGMA cache_size=10000;"))
                db.engine.execute(text("PRAGMA temp_store=memory;"))
            except Exception as e:
                print(f"Warning: Could not set SQLite optimizations: {e}")
        
        db.create_all()
        
        # Create default platforms if they don't exist
        if not Platform.query.filter_by(platform_name='Spotify').first():
            spotify = Platform(platform_name='Spotify', api_details='{"api_url": "https://api.spotify.com"}')
            db.session.add(spotify)
        
        if not Platform.query.filter_by(platform_name='YouTube').first():
            youtube = Platform(platform_name='YouTube', api_details='{"api_url": "https://www.youtube.com"}')
            db.session.add(youtube)
        
        db.session.commit()
    
    # Run with appropriate settings for environment
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)
