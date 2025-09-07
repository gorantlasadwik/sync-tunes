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
from thefuzz import fuzz, process
from groq import Groq
from ytmusicapi import YTMusic

# Load environment variables
load_dotenv()

# Configure Gemini API
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
# Note: Gemini quota tracking moved to user-specific session storage

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    print(f"✅ Gemini API configured with key: {GEMINI_API_KEY[:10]}...")

# Configure Groq API
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
if GROQ_API_KEY:
    print(f"✅ Groq API configured with key: {GROQ_API_KEY[:10]}...")
else:
    print("⚠️ Groq API key not found - will use fallback parsing only")

# Initialize YouTube Music API
try:
    ytmusic = YTMusic()
    print("✅ YouTube Music API initialized successfully")
except Exception as e:
    print(f"⚠️ YouTube Music API initialization failed: {e}")
    ytmusic = None

def check_and_reset_gemini_quota():
    """Check if 24 hours have passed since last quota reset and reset if needed - USER SPECIFIC"""
    current_time = datetime.now()
    
    # Get user-specific quota status from session
    quota_exceeded = session.get('gemini_quota_exceeded', False)
    quota_reset_time = session.get('gemini_quota_reset_time')
    
    # If quota is exceeded and we haven't reset in 24 hours, reset it
    if quota_exceeded and quota_reset_time:
        time_since_reset = current_time - datetime.fromisoformat(quota_reset_time)
        if time_since_reset.total_seconds() >= 24 * 60 * 60:  # 24 hours
            session['gemini_quota_exceeded'] = False
            session['gemini_quota_reset_time'] = current_time.isoformat()
            print(f"🔄 Gemini quota automatically reset after 24 hours for user {current_user.user_id}")
    
    # If quota is exceeded but we haven't set a reset time, set it now
    elif quota_exceeded and not quota_reset_time:
        session['gemini_quota_reset_time'] = current_time.isoformat()
        print(f"⏰ Gemini quota exceeded - will auto-reset in 24 hours for user {current_user.user_id}")

# ============================================================================
# NEW SONG EXTRACTION SYSTEM - EXACT PRIORITY ORDER
# ============================================================================

def get_licensed_metadata(video_metadata):
    """Step 1: Licensed Metadata (YouTube video page)"""
    if not video_metadata:
        return None
    
    # Check for "Licensed to YouTube by" metadata
    if "licensed" in str(video_metadata).lower():
        # Extract licensed information if available
        # This would need to be implemented based on how you get video metadata
        print("🎵 Found licensed metadata")
        return video_metadata.get("licensed_info")
    
    return None

def get_from_ytmusic(query):
    """Step 2: YouTube Music API (ytmusicapi)"""
    if not ytmusic:
        print("⚠️ YouTube Music API not available")
        return None
    
    try:
        print(f"🎵 Searching YouTube Music for: '{query}'")
        results = ytmusic.search(query, filter="songs")
        
        if results and len(results) > 0:
            top = results[0]
            song_name = top.get('title', '').strip()
            artist_name = top.get('artists', [{}])[0].get('name', '').strip()
            
            if song_name and artist_name:
                print(f"✅ YouTube Music found: '{song_name}' by '{artist_name}'")
                return {
                    'title': song_name,
                    'artist': artist_name,
                    'album': top.get('album', {}).get('name', ''),
                    'source': 'ytmusic'
                }
        
        print("❌ No good YouTube Music results found")
        return None
        
    except Exception as e:
        print(f"❌ YouTube Music API error: {e}")
        return None

def clean_title_regex(title: str):
    """Step 3: Regex Cleaning (Fallback Parser)"""
    if not title:
        return None
    
    print(f"🧹 Regex cleaning: '{title}'")
    
    # Remove brackets and common junk words
    cleaned = re.sub(r"[\(\[].*?[\)\]]", "", title)
    
    # Remove common junk words
    junk_words = [
        "official video", "lyrics", "audio", "live", "remix", "cover", 
        "slowed", "reverb", "extended", "full song", "hd", "4k", 
        "music video", "official audio", "official", "video", "song"
    ]
    
    for word in junk_words:
        cleaned = re.sub(word, "", cleaned, flags=re.IGNORECASE)
    
    # Clean up extra spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Split on "-" or "|" to find song name
    parts = re.split(r"[-|]", cleaned)
    if len(parts) > 1:
        # Usually the second part is the song name
        song = parts[1].strip()
        artist = parts[0].strip()
    else:
        # Single part - use as song name
        song = parts[0].strip()
        artist = "Unknown Artist"
    
    if song and len(song) > 2:
        print(f"✅ Regex cleaned: '{song}' by '{artist}'")
        return {
            'title': song,
            'artist': artist,
            'source': 'regex'
        }
    
    print("❌ Regex cleaning failed")
    return None

def ai_extract_song_simple(title, description=""):
    """Step 4: AI Extraction (Gemini / Groq) - Return only song name"""
    if not title:
        return None
    
    print(f"🤖 AI extraction for: '{title}'")
    
    # Try Gemini first
    if GEMINI_API_KEY and not session.get('gemini_quota_exceeded', False):
        try:
            check_and_reset_gemini_quota()
            if not session.get('gemini_quota_exceeded', False):
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                prompt = f"""
Extract ONLY the song name (not artist, not extra info) from this YouTube video title:

Title: {title}
Description: {description[:200] if description else "No description"}

Return ONLY the song name as plain text. No quotes, no extra words, no artist names.
Just the song title.

Example:
Input: "Ed Sheeran - Shape of You (Official Music Video)"
Output: Shape of You

Input: "Arijit Singh - Tum Hi Ho (Official Video) | Aashiqui 2"
Output: Tum Hi Ho
"""

                response = model.generate_content(prompt)
                song_name = response.text.strip()
                
                # Clean the response
                song_name = re.sub(r'^["\']|["\']$', '', song_name)  # Remove quotes
                song_name = song_name.strip()
                
                if song_name and len(song_name) > 2:
                    print(f"✅ Gemini extracted: '{song_name}'")
                    return {
                        'title': song_name,
                        'artist': 'Unknown Artist',
                        'source': 'gemini'
                    }
                    
        except Exception as e:
            if "quota" in str(e).lower():
                session['gemini_quota_exceeded'] = True
                print(f"⚠️ Gemini quota exceeded: {e}")
            else:
                print(f"❌ Gemini error: {e}")
    
    # Try Groq as fallback
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            
            prompt = f"""
Extract ONLY the song name from this YouTube video title:

Title: {title}

Return ONLY the song name as plain text. No quotes, no extra words.
Just the song title.

Example:
Input: "Ed Sheeran - Shape of You (Official Music Video)"
Output: Shape of You
"""

            response = client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100
            )
            
            song_name = response.choices[0].message.content.strip()
            song_name = re.sub(r'^["\']|["\']$', '', song_name)
            song_name = song_name.strip()
            
            if song_name and len(song_name) > 2:
                print(f"✅ Groq extracted: '{song_name}'")
                return {
                    'title': song_name,
                    'artist': 'Unknown Artist',
                    'source': 'groq'
                }
                
        except Exception as e:
            print(f"❌ Groq error: {e}")
    
    print("❌ AI extraction failed")
    return None

def fuzzy_match_spotify(song_name, spotify_results, threshold=80):
    """Step 5: Fuzzy Matching (thefuzz)"""
    if not song_name or not spotify_results:
        return None, 0
    
    print(f"🔍 Fuzzy matching: '{song_name}' against {len(spotify_results)} Spotify results")
    
    # Create choices for fuzzy matching
    choices = []
    for result in spotify_results:
        track_name = result.get('name', '')
        artist_name = result.get('artists', [{}])[0].get('name', '')
        choices.append(f"{track_name} - {artist_name}")
    
    if not choices:
        return None, 0
    
    # Find best match
    match, score = process.extractOne(song_name, choices, scorer=fuzz.token_sort_ratio)
    
    print(f"🎯 Best match: '{match}' (score: {score}%)")
    
    if score >= threshold:
        print(f"✅ Auto-accept: Score {score}% >= {threshold}%")
        return match, score
    else:
        print(f"⚠️ Needs confirmation: Score {score}% < {threshold}%")
        return match, score

def extract_song_new(video_title, video_description="", channel_title="", video_metadata=None):
    """Main orchestrator - Exact Priority Order Implementation"""
    print(f"\n🎵 NEW EXTRACTION SYSTEM for: '{video_title}'")
    
    # Step 1: Licensed Metadata (if available)
    licensed = get_licensed_metadata(video_metadata)
    if licensed:
        print("✅ Using licensed metadata")
        return licensed
    
    # Step 2: YouTube Music API
    ytmusic_result = get_from_ytmusic(video_title)
    if ytmusic_result:
        print("✅ Using YouTube Music API result")
        return ytmusic_result
    
    # Step 3: Regex Cleaning
    regex_result = clean_title_regex(video_title)
    if regex_result:
        print("✅ Using regex cleaning result")
        return regex_result
    
    # Step 4: AI Extraction
    ai_result = ai_extract_song_simple(video_title, video_description)
    if ai_result:
        print("✅ Using AI extraction result")
        return ai_result
    
    # Step 5: Fallback - return basic cleaned title
    fallback_title = re.sub(r'[\(\[].*?[\)\]]', '', video_title).strip()
    fallback_title = re.sub(r'\s*(official|lyrics|video|audio|hd|4k|full|song|music)', '', fallback_title, flags=re.IGNORECASE)
    
    print("⚠️ Using fallback extraction")
    return {
        'title': fallback_title,
        'artist': channel_title or 'Unknown Artist',
        'source': 'fallback'
    }

def hybrid_song_parsing(original_title, channel_title=None, video_id=None, access_token=None):
    """NEW EXTRACTION SYSTEM - Exact Priority Order Implementation"""
    
    print(f"=== NEW EXTRACTION SYSTEM START ===")
    print(f"Original title: '{original_title}'")
    print(f"Channel: '{channel_title or 'Unknown'}'")
    print(f"Video ID: '{video_id or 'None'}'")
    
    # Use the new extraction system
    extraction_result = extract_song_new(
        video_title=original_title,
        video_description="",  # Could be enhanced to get video description
        channel_title=channel_title,
        video_metadata=None  # Could be enhanced to get video metadata
    )
    
    if extraction_result:
        print(f"✅ NEW EXTRACTION SUCCESSFUL: {extraction_result['title']} by {extraction_result['artist']} (source: {extraction_result['source']})")
        return {
            'success': True,
            'method': extraction_result['source'],
            'song_name': extraction_result['title'],
            'artist_name': extraction_result['artist'],
            'album_name': extraction_result.get('album', ''),
            'spotify_track': None,  # Will search Spotify with this info
            'confidence': 0.9 if extraction_result['source'] in ['ytmusic', 'licensed'] else 0.7
        }
    
    # If new extraction system fails, return failure
    print(f"❌ NEW EXTRACTION SYSTEM FAILED")
    return {
        'success': False,
        'method': 'extraction_failed',
        'song_name': original_title,
        'artist_name': channel_title or 'Unknown Artist',
        'album_name': 'Unknown',
        'spotify_track': None,
        'confidence': 0.0,
        'fallback_results': []
    }

def search_spotify_with_cleaned_title(song_name, artist_name, access_token=None):
    """Search Spotify with pre-cleaned title and artist"""
    try:
        # Use provided token or fallback to session token
        token = access_token or session.get('spotify_token')
        if not token:
            print("No Spotify token available for search")
            return None
            
        # Initialize Spotify client
        sp = spotipy.Spotify(auth=token)
        
        # Try multiple search strategies
        search_queries = [
            f'track:"{song_name}" artist:"{artist_name}"',
            f'"{song_name}" "{artist_name}"',
            f'track:"{song_name}"',
            f'"{song_name}"'
        ]
        
        for query in search_queries:
            print(f"Trying Spotify search: '{query}'")
            results = sp.search(q=query, type='track', limit=5)
            
            if results['tracks']['items']:
                # Return the first result (most relevant)
                track = results['tracks']['items'][0]
                print(f"Found Spotify track: '{track['name']}' by '{track['artists'][0]['name']}'")
                return track
        
        print("No Spotify results found")
        return None
        
    except Exception as e:
        print(f"Spotify search error: {e}")
        return None

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
                    # Create or get song (USER-SPECIFIC)
                    song = Song.query.filter_by(
                        user_id=user_id,  # ✅ USER ISOLATION
                        title=track['name'],
                        artist=track['artists'][0]['name'] if track['artists'] else 'Unknown Artist'
                    ).first()
                    
                    if not song:
                        song = Song(
                            user_id=user_id,  # ✅ USER ISOLATION
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
                            
                            # LAZY LOADING: Store original title as-is, process later during sync
                            # This prevents API overload during playlist fetching
                            parsed_song_name = raw_title  # Store original title
                            parsed_artist = channel_title or 'Unknown Artist'
                            
                            # Log the parsing for debugging
                            print(f"YouTube title parsing (bulk): '{raw_title}' -> Song: '{parsed_song_name}', Artist: '{parsed_artist}'")
                            
                            # Create or get song (USER-SPECIFIC) - Store original title directly
                            song = Song.query.filter_by(
                                user_id=user_id,  # ✅ USER ISOLATION
                                title=parsed_song_name,  # This is now the original YouTube title
                                artist=parsed_artist
                            ).first()
                            
                            if not song:
                                song = Song(
                                    user_id=user_id,  # ✅ USER ISOLATION
                                    title=parsed_song_name,  # Original YouTube title
                                    artist=parsed_artist,
                                    album="YouTube",  # Mark as YouTube source
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
    session['gemini_quota_exceeded'] = False
    session['gemini_quota_reset_time'] = None
    print("🔄 Gemini quota flag reset - ready to use new API key")

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
        'title_simple_ratio': title_simple_ratio,
        'artist_simple_ratio': artist_simple_ratio,
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
                print(f"Processing song: '{song_info['title']}' by '{song_info['artist']}' (source: {song_info.get('source', 'unknown')})")
                
                # Check if we already have a Spotify track from hybrid parsing
                if song_info.get('spotify_track'):
                    print(f"✅ Using pre-found Spotify track: {song_info['spotify_track']['name']}")
                    print(f"🔍 Debug - Playlist ID: {playlist.platform_playlist_id}")
                    print(f"🔍 Debug - Track URI: {song_info['spotify_track']['uri']}")
                    try:
                        result = sp.playlist_add_items(playlist.platform_playlist_id, [song_info['spotify_track']['uri']])
                        songs_added += 1
                        print(f"✅ Auto-added good match: '{song_info['title']}' -> '{song_info['spotify_track']['name']}'")
                        print(f"🔍 Debug - Spotify API result: {result}")
                        
                        # Verify the song was actually added by checking playlist
                        try:
                            playlist_tracks = sp.playlist_tracks(playlist.platform_playlist_id, limit=1, offset=0)
                            print(f"🔍 Debug - Playlist now has {playlist_tracks['total']} tracks")
                        except Exception as verify_error:
                            print(f"🔍 Debug - Could not verify playlist: {verify_error}")
                        
                        continue
                    except Exception as e:
                        print(f"❌ Error adding pre-found track: {e}")
                        print(f"🔍 Debug - Playlist ID: {playlist.platform_playlist_id}")
                        print(f"🔍 Debug - Track URI: {song_info['spotify_track']['uri']}")
                        print(f"🔍 Debug - Error type: {type(e).__name__}")
                        # Continue to regular search
                
                # Note: Manual selection songs are now handled in sync_playlist_songs function
                # This function only receives songs that are ready to be added to Spotify
                
                # Regular search approach: Try artist first, then album, then song name only
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
                    
                    # Debug logging
                    print(f"🔍 Fuzzy matching debug:")
                    print(f"  Original: '{song_info['title']}' by '{song_info.get('artist', 'Unknown')}'")
                    print(f"  Spotify:  '{track['name']}' by '{track['artists'][0]['name']}'")
                    print(f"  Title similarity: {fuzzy_scores.get('title_simple_ratio', 0)}%")
                    print(f"  Artist similarity: {fuzzy_scores.get('artist_simple_ratio', 0)}%")
                    print(f"  Composite score: {fuzzy_scores.get('composite_score', 0):.3f}")
                    
                    # Additional validation for problematic matches
                    spotify_title = track['name'].strip()
                    spotify_artist = track['artists'][0]['name'].strip()
                    
                    # Reject matches with empty or very short titles
                    if not spotify_title or len(spotify_title) < 2:
                        print(f"❌ Rejecting match: Empty or too short Spotify title")
                        continue
                    
                    # Reject matches where titles are completely different
                    if fuzzy_scores.get('title_simple_ratio', 0) < 30:
                        print(f"❌ Rejecting match: Title similarity too low ({fuzzy_scores.get('title_simple_ratio', 0)}%)")
                        continue
                    
                    # Calculate overall confidence score
                    overall_confidence = calculate_confidence_score(
                        song_info.get('gemini_confidence', 0.5),
                        fuzzy_scores,
                        used_strategy,
                        song_info.get('channel_name')
                    )
                    
                    # Confidence-based triage (STRICTER THRESHOLDS)
                    if overall_confidence >= 0.95:  # Very high confidence only
                        match_quality = "HIGH"
                        is_good_match = True
                    elif overall_confidence >= 0.90:  # High confidence
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
                            session[f'pending_tracks_{current_user.user_id}'] = []
                        
                        # Calculate title similarity for user comparison
                        original_title = song_info.get('original_title', song_info['title'])
                        spotify_title = track['name']
                        title_similarity = fuzz.ratio(original_title.lower(), spotify_title.lower())
                        
                        session[f'pending_tracks_{current_user.user_id}'].append({
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
                    
                        # Try fallback search with Gemini re-analysis of full YouTube title
                        print(f"All strategies failed, asking Gemini to re-analyze full YouTube title...")
                        
                        # Initialize pending_tracks for fallback results
                        if f'pending_tracks_{current_user.user_id}' not in session:
                            session[f'pending_tracks_{current_user.user_id}'] = []
                        pending_tracks = session[f'pending_tracks_{current_user.user_id}']
                        
                        # Get the original YouTube title for re-analysis
                        original_title = song_info.get('original_title', song_info['title'])
                        channel_name = song_info.get('channel_name', 'Unknown')
                        
                    # Use new extraction system to re-analyze the full YouTube title
                    extraction_result = extract_song_new(
                        video_title=original_title,
                        video_description="",
                        channel_title=channel_name,
                        video_metadata=None
                    )
                    
                    if extraction_result:
                        corrected_song_name = extraction_result['title']
                        print(f"New extraction system re-analysis: '{original_title}' -> '{corrected_song_name}'")
                    else:
                        # Fallback to basic cleaning
                        corrected_song_name = re.sub(r'[\(\[].*?[\)\]]', '', original_title).strip()
                        corrected_song_name = re.sub(r'\s*(official|lyrics|video|audio|hd|4k|full|song|music)', '', corrected_song_name, flags=re.IGNORECASE)
                        print(f"Fallback cleaning: '{original_title}' -> '{corrected_song_name}'")
                        
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
                print(f"Error processing song '{song_info['title']}': {song_error}")
                continue
        
        # Final verification - check total tracks in playlist
        try:
            final_playlist_check = sp.playlist_tracks(playlist.platform_playlist_id, limit=1, offset=0)
            print(f"🔍 FINAL VERIFICATION - Playlist '{playlist.name}' now has {final_playlist_check['total']} total tracks")
        except Exception as final_error:
            print(f"🔍 FINAL VERIFICATION - Could not check final playlist count: {final_error}")
        
        return songs_added
        
    except Exception as e:
        print(f"Error updating Spotify playlist: {e}")
        return 0

def update_youtube_playlist_direct(access_token, target_playlist, songs_to_add, source_playlist):
    """Direct YouTube → YouTube sync using video IDs (no search needed)"""
    print(f"=== update_youtube_playlist_direct CALLED ===")
    print(f"Target playlist: {target_playlist.name}")
    print(f"Songs to add: {len(songs_to_add)}")
    print("🎯 Using direct video ID mapping - no search required!")
    
    try:
        import requests
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        youtube_playlist_id = target_playlist.platform_playlist_id
        if not youtube_playlist_id:
            print(f"ERROR: No YouTube playlist ID found for target playlist '{target_playlist.name}'")
            return 0
        
        songs_added = 0
        
        for song_info in songs_to_add:
            try:
                # Get the YouTube video ID from the source playlist's PlatformSong
                platform_song = PlatformSong.query.join(Platform).filter(
                    PlatformSong.song_id == song_info['song_id'],
                    Platform.platform_name == 'YouTube',
                    PlatformSong.platform_id == Platform.platform_id
                ).first()
                
                if not platform_song or not platform_song.platform_specific_id:
                    print(f"❌ No video ID found for song: {song_info['title']}")
                    continue
                
                video_id = platform_song.platform_specific_id
                print(f"🎯 Direct mapping: '{song_info['title']}' → Video ID: {video_id}")
                
                # Add video directly to target playlist using video ID
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
                
                print(f"YouTube direct add response: {add_response.status_code}")
                if add_response.status_code == 200:
                    songs_added += 1
                    print(f"✅ Direct added: '{song_info['title']}' (Video ID: {video_id})")
                elif add_response.status_code == 409:
                    print(f"⚠️ Video already exists in playlist: '{song_info['title']}' (Video ID: {video_id})")
                    songs_added += 1  # Count as success since it's already there
                else:
                    print(f"❌ Failed to add '{song_info['title']}': {add_response.text}")
                    
            except Exception as song_error:
                print(f"❌ Error adding song '{song_info['title']}': {song_error}")
                continue
        
        print(f"🎯 Direct sync completed: {songs_added}/{len(songs_to_add)} songs added")
        return songs_added
        
    except Exception as e:
        print(f"❌ Direct YouTube sync failed: {str(e)}")
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
            if song and song.user_id == current_user.user_id:  # ✅ USER ISOLATION CHECK
                source_songs.append({
                    'song_id': song.song_id,  # Add song_id for tracking
                    'title': song.title,
                    'artist': song.artist,
                    'album': song.album,
                    'duration': song.duration
                })
        
        print(f"🔄 Starting sync: {len(source_songs)} songs from {source_platform} to {target_platform}")
        
        # Create sync log entry BEFORE starting sync
        sync_log = SyncLog(
            user_id=current_user.user_id,
            source_account_id=source_account.account_id,
            destination_account_id=target_account.account_id,
            playlist_id=source_playlist.playlist_id,
            total_songs_synced=len(source_songs),
            songs_added=0,  # Will be updated after sync
            songs_removed=0,
            timestamp=datetime.now().date()
        )
        db.session.add(sync_log)
        db.session.flush()  # Get the sync_id
        
        # Add songs to target platform
        songs_added = 0
        songs_failed = 0
        
        # 🚀 OPTIMIZATION: Direct YouTube → YouTube sync using video IDs
        if source_platform == 'YouTube' and target_platform == 'YouTube':
            print("🎯 YouTube → YouTube sync detected: Using direct video ID mapping")
            songs_added = update_youtube_playlist_direct(target_account.auth_token, target_playlist, source_songs, source_playlist)
        elif target_platform == 'Spotify':
            songs_added = update_spotify_playlist(target_account.auth_token, target_playlist, source_songs)
        elif target_platform == 'YouTube':
            songs_added = update_youtube_playlist(target_account.auth_token, target_playlist, source_songs)
        
        # Calculate failed songs
        songs_failed = len(source_songs) - songs_added
        
        # Update sync log with actual results
        sync_log.songs_added = songs_added
        sync_log.songs_removed = 0  # Cross-platform sync doesn't remove songs
        
        # Create individual song tracking entries
        for i, song_data in enumerate(source_songs):
            if i < songs_added:
                # Song was successfully added
                sync_song = SyncSong(
                    sync_id=sync_log.sync_id,
                    song_id=song_data['song_id'],
                    action='added',
                    timestamp=datetime.now()
                )
                db.session.add(sync_song)
            else:
                # Song failed to be added
                sync_song = SyncSong(
                    sync_id=sync_log.sync_id,
                    song_id=song_data['song_id'],
                    action='failed',
                    timestamp=datetime.now()
                )
                db.session.add(sync_song)
        
        db.session.commit()
        
        print(f"✅ Sync completed: {songs_added} added, {songs_failed} failed")
        
        return True, f"Successfully synced {songs_added} songs from {source_platform} to {target_platform} (Failed: {songs_failed})"
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Sync failed: {str(e)}")
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
    user_id = db.Column(db.Integer, db.ForeignKey('User_.user_id'), nullable=False)  # ✅ USER ISOLATION
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
    action = db.Column(db.String(10), nullable=False)  # 'added', 'removed', or 'failed'
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
            # Clear any existing session data to prevent cross-user contamination
            session.clear()
            print(f"🧹 Cleared session data for new login: {user.email}")
            login_user(user)
            return redirect(url_for('dashboard'))
        
        # Try to find admin
        admin = Admin.query.filter_by(email=email).first()
        if admin and check_password_hash(admin.password, password):
            # Clear any existing session data to prevent cross-user contamination
            session.clear()
            print(f"🧹 Cleared session data for new admin login: {admin.email}")
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
            # Generate a unique state parameter for this user's OAuth flow
            import secrets
            state = secrets.token_urlsafe(32)
            
            # Store the state in session with user-specific key
            session[f'spotify_oauth_state_{current_user.user_id}'] = state
            print(f"🔐 Generated Spotify OAuth state for user {current_user.user_id}: {state[:10]}...")
            
            # Clear any existing Spotify cache files to prevent token contamination
            import os
            cache_file = f".spotify_cache_user_{current_user.user_id}.cache"
            if os.path.exists(cache_file):
                os.remove(cache_file)
                print(f"🗑️ Cleared existing Spotify cache file for user {current_user.user_id}")
            
            # Redirect to Spotify OAuth with user-specific cache path
            spotify_oauth = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope='playlist-read-private playlist-read-collaborative user-read-private playlist-modify-public playlist-modify-private',
                cache_path=f".spotify_cache_user_{current_user.user_id}.cache"
            )
            auth_url = spotify_oauth.get_authorize_url(state=state)
            return redirect(auth_url)
        
        elif platform_name == 'YouTube':
            # Redirect to Google OAuth for YouTube
            try:
                platform = Platform.query.filter_by(platform_name='YouTube').first()
                if not platform:
                    platform = Platform(platform_name='YouTube', api_details='{"api_url": "https://www.youtube.com", "version": "v3"}')
                    db.session.add(platform)
                    db.session.commit()
                
                # Generate a unique state parameter for this user's OAuth flow
                import secrets
                state = secrets.token_urlsafe(32)
                
                # Store the state in session with user-specific key
                session[f'youtube_oauth_state_{current_user.user_id}'] = state
                print(f"🔐 Generated YouTube OAuth state for user {current_user.user_id}: {state[:10]}...")
                
                # Build Google OAuth URL
                from urllib.parse import urlencode
                params = {
                    'client_id': YOUTUBE_CLIENT_ID,
                    'redirect_uri': YOUTUBE_REDIRECT_URI,
                    'scope': 'https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.force-ssl',
                    'response_type': 'code',
                    'access_type': 'offline',
                    'state': state
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
        state = request.args.get('state')
        
        if not code:
            flash('Spotify authorization failed')
            return redirect(url_for('dashboard'))
        
        # Validate state parameter to prevent cross-user contamination
        expected_state = session.get(f'spotify_oauth_state_{current_user.user_id}')
        if not state or not expected_state or state != expected_state:
            print(f"❌ Invalid or missing state parameter for user {current_user.user_id}")
            print(f"Expected: {expected_state}, Received: {state}")
            flash('Spotify authorization failed: Invalid state parameter')
            return redirect(url_for('dashboard'))
        
        # Clear the state parameter after validation
        session.pop(f'spotify_oauth_state_{current_user.user_id}', None)
        print(f"✅ Validated Spotify OAuth state for user {current_user.user_id}")
        
        # Clear any existing Spotify session data to prevent cross-user contamination
        session.pop('spotify_token', None)
        session.pop('spotify_user_info', None)
        print(f"🧹 Cleared existing Spotify session data for user {current_user.user_id}")
        
        # Clear any existing Spotify cache files to prevent token contamination
        import os
        cache_file = f".spotify_cache_user_{current_user.user_id}.cache"
        if os.path.exists(cache_file):
            os.remove(cache_file)
            print(f"🗑️ Cleared existing Spotify cache file for user {current_user.user_id}")
        
        # Exchange code for access token
        print(f"Spotify OAuth config - Client ID: {SPOTIFY_CLIENT_ID[:10]}...")
        print(f"Spotify OAuth config - Redirect URI: {SPOTIFY_REDIRECT_URI}")
        print(f"🔐 Processing Spotify callback for user: {current_user.user_id}")
        
        spotify_oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope='playlist-read-private playlist-read-collaborative user-read-private playlist-modify-public playlist-modify-private',
            cache_path=f".spotify_cache_user_{current_user.user_id}.cache"
        )
        
        token_info = spotify_oauth.get_access_token(code)
        access_token = token_info['access_token']
        print(f"Spotify access token obtained: {access_token[:20]}...")
        
        # Get user info from Spotify with error handling
        sp = spotipy.Spotify(auth=access_token)
        try:
            user_info = sp.current_user()
            print(f"Spotify callback - user info: {user_info}")
            print(f"✅ Spotify user: {user_info.get('display_name', 'Unknown')} (ID: {user_info.get('id', 'Unknown')})")
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
        state = request.args.get('state')
        
        if not code:
            flash('YouTube authorization failed')
            return redirect(url_for('dashboard'))
        
        # Validate state parameter to prevent cross-user contamination
        expected_state = session.get(f'youtube_oauth_state_{current_user.user_id}')
        if not state or not expected_state or state != expected_state:
            print(f"❌ Invalid or missing state parameter for user {current_user.user_id}")
            print(f"Expected: {expected_state}, Received: {state}")
            flash('YouTube authorization failed: Invalid state parameter')
            return redirect(url_for('dashboard'))
        
        # Clear the state parameter after validation
        session.pop(f'youtube_oauth_state_{current_user.user_id}', None)
        print(f"✅ Validated YouTube OAuth state for user {current_user.user_id}")
        
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
        songs_not_found = 0  # Track songs that don't exist in database
        songs_to_add_to_platform = []
        synced_song_ids = []  # Track which songs were actually synced
        
        for song_id in song_ids:
            song = db.session.get(Song, song_id)
            if song:
                # Always add to database (PlaylistSong table) - this tracks our sync history
                existing = PlaylistSong.query.filter_by(
                    playlist_id=target_playlist.playlist_id,
                    song_id=song.song_id
                ).first()
                
                # Always prepare for platform API call (regardless of database status)
                # This ensures songs are added to the actual Spotify playlist even if they exist in our database
                
                # Add to database if not already there
                if not existing:
                    playlist_song = PlaylistSong(
                        playlist_id=target_playlist.playlist_id,
                        song_id=song.song_id,
                        added_at=datetime.now().date()
                    )
                    db.session.add(playlist_song)
                
                # Always count as processed (whether new or existing)
                songs_added += 1
                synced_song_ids.append(song.song_id)  # Track this synced song
                
                # If syncing from YouTube to another platform, use hybrid approach
                if source_platform.platform_name == 'YouTube' and platform.platform_name != 'YouTube':
                    # Get the original YouTube title from the platform song mapping
                    platform_song = PlatformSong.query.filter_by(
                        song_id=song.song_id,
                        platform_id=source_platform.platform_id
                    ).first()
                    
                    if platform_song:
                        # For YouTube songs, the title is already the original YouTube title
                        original_title = song.title  # Title is now the original YouTube title
                        video_id = platform_song.platform_specific_id
                        print(f"Processing YouTube title: '{original_title}'")
                            
                        # Use hybrid parsing approach (NEW EXTRACTION SYSTEM)
                        hybrid_result = hybrid_song_parsing(original_title, song.artist, video_id, target_user_account.auth_token)
                            
                        if hybrid_result['success']:
                            # Success - add to platform
                            print(f"✅ Hybrid parsing successful: {hybrid_result['song_name']} by {hybrid_result['artist_name']} (method: {hybrid_result['method']})")
                            
                            songs_to_add_to_platform.append({
                                'title': hybrid_result['song_name'],
                                'artist': hybrid_result['artist_name'],
                                'album': hybrid_result['album_name'],
                                'original_title': original_title,
                                'duration': song.duration,
                                'gemini_confidence': hybrid_result['confidence'],
                                'channel_name': song.artist,
                                'source': hybrid_result['method'],
                                'spotify_track': hybrid_result.get('spotify_track'),
                                'fallback_results': hybrid_result.get('fallback_results', [])
                            })
                        else:
                            # Manual selection required
                            print(f"⚠️ Manual selection required for: {hybrid_result['song_name']} by {hybrid_result['artist_name']}")
                            
                            songs_to_add_to_platform.append({
                                'title': hybrid_result['song_name'],
                                'artist': hybrid_result['artist_name'],
                                'album': hybrid_result['album_name'],
                                'original_title': original_title,
                                'duration': song.duration,
                                'gemini_confidence': 0.0,
                                'channel_name': song.artist,
                                'source': 'manual_selection',
                                'spotify_track': None,
                                'fallback_results': hybrid_result.get('fallback_results', [])
                            })
                else:
                    # For other sync types, use original song data
                    songs_to_add_to_platform.append({
                        'title': song.title,
                        'artist': song.artist,
                        'album': song.album,
                        'duration': song.duration
                    })
                    
                    # Commit database changes for this song
                    db.session.commit()
            else:
                # Song doesn't exist in database - this shouldn't happen in normal operation
                print(f"Warning: Song ID {song_id} not found in database - skipping this song")
                songs_not_found += 1
                # Skip this song and continue with the next one
                continue
        
        # After processing all songs, separate songs that need manual selection from songs ready to be added
        songs_ready_for_platform = []
        pending_tracks = []
        
        for song_info in songs_to_add_to_platform:
            if song_info.get('source') in ['manual_selection', 'ai_comparison']:
                # Store for manual selection or AI comparison (minimal data to avoid session cookie size issues)
                pending_tracks.append({
                    'song_info': {
                        'title': song_info.get('title'),
                        'artist': song_info.get('artist'),
                        'album': song_info.get('album'),
                        'original_title': song_info.get('original_title')
                    },
                    'spotify_track': None,
                    'confidence': 0.0,
                    'search_strategy': song_info.get('source'),
                    'fallback_results': song_info.get('fallback_results', [])[:3] if song_info.get('fallback_results') else [],  # Limit to 3 results
                    'ai_results': song_info.get('ai_results', {}),
                    'target_playlist_id': target_playlist.platform_playlist_id,
                    'target_playlist_name': target_playlist.name
                })
            else:
                # Ready to be added to platform
                songs_ready_for_platform.append(song_info)
        
        # Store pending tracks in session (user-specific)
        if pending_tracks:
            user_pending_key = f'pending_tracks_{current_user.user_id}'
            if user_pending_key not in session:
                session[user_pending_key] = []
            session[user_pending_key].extend(pending_tracks)
            session.modified = True
        
        # Try to update the real platform playlist (only for songs ready to be added)
        platform_songs_added = 0
        # Log to file for better debugging
        with open('/tmp/sync_debug.log', 'a') as f:
            f.write(f"=== SYNC DEBUG START ===\n")
            f.write(f"Sync debug - Platform: {platform.platform_name if platform else 'None'}\n")
            f.write(f"Sync debug - Target account token: {'Present' if target_user_account.auth_token else 'Missing'}\n")
            f.write(f"Songs ready for platform: {len(songs_ready_for_platform)}\n")
            f.write(f"Songs requiring manual selection: {len(pending_tracks)}\n")
            f.write(f"Target playlist: {target_playlist.name if target_playlist else 'None'}\n")
            f.write(f"Target playlist platform ID: {target_playlist.platform_playlist_id if target_playlist else 'None'}\n")
        
        if platform and target_user_account.auth_token and songs_ready_for_platform:
            if platform.platform_name == 'YouTube':
                platform_songs_added = update_youtube_playlist(
                    target_user_account.auth_token, 
                    target_playlist, 
                    songs_ready_for_platform
                )
            elif platform.platform_name == 'Spotify':
                platform_songs_added = update_spotify_playlist(
                    target_user_account.auth_token, 
                    target_playlist, 
                    songs_ready_for_platform
                )
        
        # Create sync log - record the TARGET playlist where songs were added
        sync_log = SyncLog(
            user_id=current_user.user_id,
            source_account_id=user_account.account_id,
            destination_account_id=target_user_account.account_id,
            playlist_id=target_playlist.playlist_id,  # Changed to target playlist
            total_songs_synced=songs_added,
            songs_added=platform_songs_added,  # Only count songs actually added to platform
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
        pending_tracks = session.get(f'pending_tracks_{current_user.user_id}', [])
        
        # Show comprehensive sync results
        messages = []
        
        if platform_songs_added > 0:
            messages.append(f'Successfully added {platform_songs_added} songs to {platform.platform_name} playlist!')
        
        if songs_skipped > 0:
            messages.append(f'{songs_skipped} songs already exist in the target playlist.')
        
        if songs_not_found > 0:
            messages.append(f'{songs_not_found} songs were not found in the database and were skipped.')
        
        if len(pending_tracks) > 0:
            messages.append(f'Found {len(pending_tracks)} songs that need your confirmation. Please review and select alternative tracks.')
        
        if not messages:
            messages.append('No songs were selected for syncing.')
        
        # Add Spotify UI update note if songs were added
        if platform_songs_added > 0:
            messages.append('Note: Spotify UI may take a few minutes to update.')
        
        flash(' '.join(messages))
        
        # If there are pending tracks (songs not found), redirect to confirmation page
        if pending_tracks:
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
        pending_tracks = session.get(f'pending_tracks_{current_user.user_id}', [])
        
        if not pending_tracks:
            flash('No pending tracks to confirm.')
            return redirect(url_for('dashboard'))
        
        # Validate data structure (handle both old and new formats)
        for i, track_data in enumerate(pending_tracks):
            # Check for new data structure
            if 'song_info' not in track_data and 'original_song' not in track_data:
                flash('Error: Invalid track data structure.')
                return redirect(url_for('dashboard'))
        
        return render_template('confirm_fallback_tracks.html', pending_tracks=pending_tracks)
        
    except Exception as e:
        flash(f'Error loading fallback tracks: {str(e)}')
        return redirect(url_for('dashboard'))

@app.route('/confirm_ai_result', methods=['POST'])
@login_required
def confirm_ai_result():
    """Confirm an AI result selection (Gemini vs Groq)"""
    try:
        track_index = int(request.form.get('track_index'))
        ai_choice = request.form.get('ai_choice')  # 'gemini' or 'groq'
        
        pending_tracks = session.get(f'pending_tracks_{current_user.user_id}', [])
        if track_index >= len(pending_tracks):
            flash('Invalid track selection.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        track_data = pending_tracks[track_index]
        ai_results = track_data.get('ai_results', {})
        
        if ai_choice not in ai_results:
            flash('Invalid AI choice.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        # Get the selected AI result
        selected_result = ai_results[ai_choice]
        
        # Get playlist ID from the stored target playlist information
        playlist_id = track_data.get('target_playlist_id')
        if not playlist_id:
            flash('Target playlist information not found.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        # Search Spotify for the AI result
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
            
            # Search Spotify for the AI result
            sp = spotipy.Spotify(auth=user_account.auth_token)
            search_query = f'track:"{selected_result["song_name"]}" artist:"{selected_result["artist_name"]}"'
            results = sp.search(q=search_query, type='track', limit=1)
            
            if results['tracks']['items']:
                spotify_track = results['tracks']['items'][0]
                
                # Add track to playlist
                sp.playlist_add_items(playlist_id, [spotify_track['uri']])
                
                # Remove this track from pending tracks
                pending_tracks.pop(track_index)
                session[f'pending_tracks_{current_user.user_id}'] = pending_tracks
                session.modified = True
                
                flash(f'Successfully added "{spotify_track["name"]}" by {spotify_track["artists"][0]["name"]} to your playlist!')
            else:
                flash(f'Could not find "{selected_result["song_name"]}" by {selected_result["artist_name"]} on Spotify.')
                
        except Exception as e:
            flash(f'Error adding track to playlist: {str(e)}')
        
        # Redirect back to confirmation page
        if pending_tracks:
            return redirect(url_for('confirm_fallback_tracks'))
        else:
            return redirect(url_for('dashboard'))
            
    except Exception as e:
        flash(f'Error processing AI result: {str(e)}')
        return redirect(url_for('confirm_fallback_tracks'))

@app.route('/confirm_track', methods=['POST'])
@login_required
def confirm_track():
    """Confirm a fallback track selection"""
    try:
        track_index = int(request.form.get('track_index'))
        
        pending_tracks = session.get(f'pending_tracks_{current_user.user_id}', [])
        if track_index >= len(pending_tracks):
            flash('Invalid track selection.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        track_data = pending_tracks[track_index]
        if not track_data['spotify_track']:
            flash('No track to add.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        selected_track = track_data['spotify_track']
        song_info = track_data['song_info']
        
        # Get playlist ID from the stored target playlist information
        playlist_id = track_data.get('target_playlist_id')
        if not playlist_id:
            flash('Target playlist information not found.')
            return redirect(url_for('confirm_fallback_tracks'))
        
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
            session[f'pending_tracks_{current_user.user_id}'] = pending_tracks
            session.modified = True
            
            # Learning mechanism: Track exact match confirmations
            if selected_track.get('is_exact_match'):
                exact_match_count = session.get(f'exact_match_confirmations_{current_user.user_id}', 0) + 1
                session[f'exact_match_confirmations_{current_user.user_id}'] = exact_match_count
                session.modified = True
                
                # Auto-enable after 5 exact match confirmations
                if exact_match_count >= 5 and not session.get(f'auto_confirm_exact_matches_{current_user.user_id}'):
                    session[f'auto_confirm_exact_matches_{current_user.user_id}'] = True
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
        
        pending_tracks = session.get(f'pending_tracks_{current_user.user_id}', [])
        if track_index >= len(pending_tracks):
            flash('Invalid track selection.')
            return redirect(url_for('confirm_fallback_tracks'))
        
        # Remove this track from pending tracks
        pending_tracks.pop(track_index)
        session[f'pending_tracks_{current_user.user_id}'] = pending_tracks
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
        session[f'auto_confirm_exact_matches_{current_user.user_id}'] = auto_confirm
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
    
    # Clear all session data to prevent cross-user contamination
    session.clear()
    print(f"🧹 Cleared all session data on logout")
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

@app.route('/migrate_user_isolation')
def migrate_user_isolation():
    """Migrate existing songs to have user_id for user isolation"""
    try:
        from sqlalchemy import text
        
        # Check if user_id column exists
        result = db.engine.execute(text("PRAGMA table_info(song)"))
        columns = [row[1] for row in result]
        
        if 'user_id' not in columns:
            # Add user_id column
            db.engine.execute(text("ALTER TABLE song ADD COLUMN user_id INTEGER"))
            print("✅ Added user_id column to song table")
        
        # Update existing songs to have user_id based on playlist ownership
        # This is a complex migration - we'll assign songs to the first user who has them in a playlist
        db.engine.execute(text("""
            UPDATE song 
            SET user_id = (
                SELECT DISTINCT ua.user_id 
                FROM playlist p 
                JOIN user_platform_account ua ON p.account_id = ua.account_id 
                JOIN playlist_song ps ON p.playlist_id = ps.playlist_id 
                WHERE ps.song_id = song.song_id 
                LIMIT 1
            )
            WHERE user_id IS NULL
        """))
        
        # For songs not in any playlist, assign to admin user (user_id = 1) or delete them
        db.engine.execute(text("""
            DELETE FROM song 
            WHERE user_id IS NULL
        """))
        
        db.session.commit()
        return 'User isolation migration completed successfully!'
        
    except Exception as e:
        db.session.rollback()
        return f'Migration error: {str(e)}'

@app.route('/debug_platforms')
@login_required
def debug_platforms():
    """Debug route to check platform data"""
    try:
        # Get all platforms
        platforms = Platform.query.all()
        platform_data = []
        for platform in platforms:
            platform_data.append({
                'platform_id': platform.platform_id,
                'platform_name': platform.platform_name,
                'api_details': platform.api_details
            })
        
        # Get user's platform accounts
        user_accounts = UserPlatformAccount.query.filter_by(user_id=current_user.user_id).all()
        account_data = []
        for account in user_accounts:
            platform = db.session.get(Platform, account.platform_id)
            account_data.append({
                'account_id': account.account_id,
                'platform_id': account.platform_id,
                'platform_name': platform.platform_name if platform else 'Unknown',
                'username': account.username_on_platform,
                'has_token': bool(account.auth_token)
            })
        
        # Get playlists with platform info
        playlists = []
        for account in user_accounts:
            platform = db.session.get(Platform, account.platform_id)
            account_playlists = Playlist.query.filter_by(account_id=account.account_id).all()
            for playlist in account_playlists:
                playlists.append({
                    'playlist_id': playlist.playlist_id,
                    'name': playlist.name,
                    'account_id': playlist.account_id,
                    'platform_id': account.platform_id,
                    'platform_name': platform.platform_name if platform else 'Unknown'
                })
        
        return jsonify({
            'platforms': platform_data,
            'user_accounts': account_data,
            'playlists': playlists
        })
    except Exception as e:
        return f'Debug error: {str(e)}', 500

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
