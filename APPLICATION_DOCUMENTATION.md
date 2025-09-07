# Sync Tunes - Complete Application Documentation

## 🎵 Overview
Sync Tunes is a web application that allows users to synchronize their music playlists between Spotify and YouTube Music platforms. It uses AI-powered song title extraction to match songs across platforms and provides intelligent playlist synchronization.

## 🏗️ Architecture

### Technology Stack
- **Backend Framework**: Flask 2.3.3
- **Database**: SQLite (local) / PostgreSQL (production)
- **Authentication**: Flask-Login with bcrypt password hashing
- **Frontend**: HTML templates with Bootstrap styling
- **Deployment**: Render.com
- **API Integrations**: Spotify Web API, YouTube Data API, YouTube Music API

### Core Dependencies
```
Flask==2.3.3                    # Web framework
Flask-SQLAlchemy==3.0.5         # Database ORM
Flask-Login==0.6.3              # User authentication
spotipy==2.23.0                 # Spotify API client
ytmusicapi==0.24.1              # YouTube Music API
google-generativeai==0.3.2      # Google Gemini AI
groq==0.4.2                     # Groq AI API
thefuzz==0.22.1                 # Fuzzy string matching
bcrypt==4.0.1                   # Password hashing
```

## 🗄️ Database Schema

### Core Models

#### 1. User Management
```python
class User(db.Model):
    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.Date, default=lambda: datetime.now().date())
    is_active = db.Column(db.Boolean, default=True)

class Admin(db.Model):
    admin_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.Date, default=lambda: datetime.now().date())
```

#### 2. Platform Management
```python
class Platform(db.Model):
    platform_id = db.Column(db.Integer, primary_key=True)
    platform_name = db.Column(db.String(100), nullable=False)  # 'Spotify' or 'YouTube'
    api_details = db.Column(db.Text)

class UserPlatformAccount(db.Model):
    account_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('User_.user_id'), nullable=False)
    platform_id = db.Column(db.Integer, db.ForeignKey('platform.platform_id'), nullable=False)
    username_on_platform = db.Column(db.String(100))
    auth_token = db.Column(db.Text)  # OAuth access token
```

#### 3. Music Data
```python
class Song(db.Model):
    song_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('User_.user_id'), nullable=False)  # USER ISOLATION
    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(150))
    album = db.Column(db.String(150))
    duration = db.Column(db.Integer)

class Playlist(db.Model):
    playlist_id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('user_platform_account.account_id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500))
    last_updated = db.Column(db.Date, default=lambda: datetime.now().date())
    platform_playlist_id = db.Column(db.String(200))  # Platform-specific playlist ID

class PlatformSong(db.Model):
    platform_song_id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.song_id'), nullable=False)
    platform_id = db.Column(db.Integer, db.ForeignKey('platform.platform_id'), nullable=False)
    platform_specific_id = db.Column(db.String(200))  # Platform-specific song ID

class PlaylistSong(db.Model):
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.playlist_id'), primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.song_id'), primary_key=True)
    added_at = db.Column(db.Date, default=lambda: datetime.now().date())
```

#### 4. Sync Tracking
```python
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
    sync_id = db.Column(db.Integer, db.ForeignKey('sync_log.sync_id'), primary_key=True)
    song_id = db.Column(db.Integer, db.ForeignKey('song.song_id'), primary_key=True)
    action = db.Column(db.String(10), nullable=False)  # 'added' or 'removed'
    timestamp = db.Column(db.DateTime, default=datetime.now)

class UserFeedback(db.Model):
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
```

## 🔌 API Integrations

### 1. Spotify Web API
- **Purpose**: Access user playlists, search songs, create playlists
- **Authentication**: OAuth 2.0 with SpotifyOAuth
- **Key Features**:
  - Fetch user's playlists and tracks
  - Search for songs by title/artist
  - Create new playlists
  - Add tracks to playlists
- **Scopes**: `playlist-read-private`, `playlist-modify-public`, `playlist-modify-private`, `user-read-private`

### 2. YouTube Data API
- **Purpose**: Access user's YouTube playlists and video information
- **Authentication**: OAuth 2.0 with Google OAuth
- **Key Features**:
  - Fetch user's YouTube playlists
  - Get video metadata and titles
  - Access channel information
- **Scopes**: `https://www.googleapis.com/auth/youtube.readonly`

### 3. YouTube Music API (ytmusicapi)
- **Purpose**: Search for structured music data
- **Authentication**: No authentication required (public API)
- **Key Features**:
  - Search for songs with structured metadata
  - Get clean song titles and artist names
  - Access to YouTube Music's music database

### 4. Google Gemini AI
- **Purpose**: Intelligent song title extraction from YouTube video titles
- **Authentication**: API Key
- **Key Features**:
  - Parse complex YouTube video titles
  - Extract song name and artist from messy titles
  - Handle various title formats and languages
- **Usage**: Primary AI for song title extraction

### 5. Groq AI
- **Purpose**: Fallback AI for song title extraction
- **Authentication**: API Key
- **Key Features**:
  - Alternative AI when Gemini quota is exceeded
  - Fast inference for song title parsing
  - Backup processing capability

## 🧠 AI-Powered Song Extraction System

### Priority Order (Exact Implementation)
The application uses a sophisticated 5-step priority system for extracting song information:

#### Step 1: Licensed Metadata
- **Source**: YouTube video page metadata
- **Process**: Extract "Licensed to YouTube by" information
- **Output**: Clean song title and artist from official metadata

#### Step 2: YouTube Music API
- **Source**: YouTube Music search results
- **Process**: Search for the video title in YouTube Music database
- **Output**: Structured song data with clean titles

#### Step 3: Regex Cleaning
- **Source**: YouTube video title
- **Process**: Advanced regex patterns to clean common title formats
- **Patterns**: 
  - Remove brackets: `[Official Video]`, `[Lyrics]`
  - Remove common suffixes: `Official Video`, `Lyrics`, `HD`, `4K`
  - Handle artist - song formats: `Artist - Song Title`

#### Step 4: AI Extraction (Gemini)
- **Source**: Cleaned title + video description
- **Process**: Google Gemini AI analysis
- **Output**: Extracted song name and artist
- **Fallback**: Groq AI if Gemini quota exceeded

#### Step 5: Fuzzy Matching
- **Source**: Extracted song name
- **Process**: Search Spotify with fuzzy matching (80% threshold)
- **Output**: Best matching Spotify track

### Confidence Scoring
- **HIGH (≥95%)**: Auto-add to playlist
- **MEDIUM (≥90%)**: Auto-add with logging
- **LOW (≥50%)**: Require user confirmation
- **REJECT (<50%)**: Skip or manual selection

## 🛣️ Application Routes

### Authentication Routes
- `GET /` - Home page
- `GET/POST /login` - User login
- `GET/POST /register` - User registration
- `GET /logout` - User logout

### Dashboard Routes
- `GET /dashboard` - Main user dashboard
- `GET /admin_dashboard` - Admin dashboard
- `GET /profile` - User profile management

### Platform Connection Routes
- `GET/POST /connect_platform` - Connect Spotify/YouTube accounts
- `GET /spotify_callback` - Spotify OAuth callback
- `GET /youtube_callback` - YouTube OAuth callback
- `GET /disconnect_platform/<id>` - Disconnect platform account

### Playlist Management Routes
- `GET /refresh_playlists` - Refresh user's playlists
- `GET /playlist_details/<id>` - View playlist details
- `POST /sync_playlist_songs` - Sync playlist songs

### Sync Operations Routes
- `POST /sync_cross_platform` - Cross-platform playlist sync
- `GET /sync_details/<id>` - View sync operation details
- `POST /confirm_ai_result` - Confirm AI extraction results
- `POST /confirm_track` - Confirm individual track matches
- `POST /skip_track` - Skip problematic tracks
- `POST /toggle_auto_confirm` - Toggle auto-confirmation settings

### Utility Routes
- `GET /logs` - View application logs
- `GET /debug_logs` - Debug logging information
- `GET /test_debug` - Test debug functionality
- `GET /cleanup_logs` - Clean up old logs
- `GET /migrate_user_isolation` - Database migration for user isolation
- `GET /debug_platforms` - Debug platform connections

## 🔄 Sync Process Flow

### 1. User Authentication
1. User logs in with email/password
2. Session is created with user isolation
3. User can connect Spotify and YouTube accounts

### 2. Playlist Discovery
1. User connects platform accounts via OAuth
2. Application fetches user's playlists from both platforms
3. Songs are stored with original titles (lazy loading)

### 3. Sync Operation
1. User selects source and destination playlists
2. For each song in source playlist:
   - Extract clean song information using AI system
   - Search for matching song in destination platform
   - Apply confidence scoring
   - Auto-add high confidence matches
   - Request user confirmation for low confidence matches
3. Create sync log with detailed tracking

### 4. User Confirmation
1. Present AI extraction results to user
2. Allow manual corrections
3. Store user feedback for ML improvement
4. Apply corrections to sync operation

## 🔒 Security Features

### User Isolation
- **Database Level**: All songs are user-specific with `user_id` foreign key
- **Session Level**: Session data is cleared on login to prevent cross-user contamination
- **Query Level**: All database queries filter by `current_user.user_id`

### Authentication Security
- **Password Hashing**: bcrypt with salt
- **Session Management**: Flask-Login with secure sessions
- **OAuth Security**: Secure token storage and validation

### API Security
- **Rate Limiting**: Built-in quota management for AI APIs
- **Token Management**: Secure storage of OAuth tokens
- **Error Handling**: Comprehensive error handling and logging

## 📊 Performance Optimizations

### Lazy Loading
- **Problem**: Processing all songs during playlist fetch caused timeouts
- **Solution**: Store original YouTube titles as-is, process only during sync
- **Result**: Fast playlist fetching, on-demand processing

### Caching Strategy
- **Session Caching**: User-specific quota tracking
- **Database Caching**: Efficient query patterns
- **API Caching**: Minimize redundant API calls

### Error Handling
- **Graceful Degradation**: Fallback to simpler parsing when AI fails
- **User Feedback**: Allow manual corrections for failed extractions
- **Logging**: Comprehensive logging for debugging and monitoring

## 🚀 Deployment

### Render.com Configuration
- **Build Command**: Automatic Python detection
- **Start Command**: `gunicorn app:app`
- **Environment**: Python 3.9+
- **Database**: PostgreSQL (production) / SQLite (development)

### Environment Variables
```bash
# Required
FLASK_SECRET_KEY=your-secret-key
GEMINI_API_KEY=your-gemini-key
GROQ_API_KEY=your-groq-key

# Spotify OAuth
SPOTIFY_CLIENT_ID=your-spotify-client-id
SPOTIFY_CLIENT_SECRET=your-spotify-client-secret
SPOTIFY_REDIRECT_URI=your-redirect-uri

# YouTube OAuth
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=your-redirect-uri

# Database (Render provides automatically)
DATABASE_URL=postgresql://...
```

## 📈 Monitoring & Analytics

### Sync Tracking
- **SyncLog**: Track all sync operations with statistics
- **SyncSong**: Track individual song actions (added/removed)
- **UserFeedback**: Collect user corrections for ML improvement

### Performance Metrics
- **Success Rates**: Track AI extraction success rates
- **User Satisfaction**: Monitor user corrections and feedback
- **API Usage**: Track quota usage and optimization opportunities

## 🔧 Maintenance

### Database Migrations
- **User Isolation Migration**: `/migrate_user_isolation` route
- **Schema Updates**: Automatic via Flask-SQLAlchemy
- **Data Cleanup**: Orphaned record removal

### Log Management
- **Log Rotation**: Automatic cleanup of old logs
- **Debug Information**: Comprehensive debugging routes
- **Error Tracking**: Detailed error logging and reporting

## 🎯 Future Enhancements

### Planned Features
1. **Machine Learning**: Use user feedback to improve AI extraction
2. **Batch Operations**: Sync multiple playlists simultaneously
3. **Smart Recommendations**: Suggest similar songs across platforms
4. **Playlist Analytics**: Usage statistics and insights
5. **Mobile App**: Native mobile application
6. **Advanced Matching**: Audio fingerprinting for better matching

### Technical Improvements
1. **Caching Layer**: Redis for improved performance
2. **Background Jobs**: Celery for async processing
3. **API Rate Limiting**: Advanced rate limiting strategies
4. **Monitoring**: Application performance monitoring
5. **Testing**: Comprehensive test suite

---

This documentation provides a complete overview of the Sync Tunes application architecture, functionality, and implementation details. The application successfully bridges the gap between Spotify and YouTube Music platforms using AI-powered song matching and intelligent playlist synchronization.
