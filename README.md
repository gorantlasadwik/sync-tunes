# 🎵 Sync Tunes

**Intelligent Playlist Synchronization Between Spotify and YouTube Music**

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.3.3-green.svg)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-Render-brightgreen.svg)](https://sync-tunes.onrender.com)

## 🌟 Overview

Sync Tunes is a powerful web application that bridges the gap between Spotify and YouTube Music platforms. Using advanced AI-powered song title extraction, it intelligently matches and synchronizes your playlists across both platforms with high accuracy.

### ✨ Key Features

- **🤖 AI-Powered Matching**: Uses Google Gemini AI and Groq for intelligent song title extraction
- **🔄 Cross-Platform Sync**: Seamlessly sync playlists between Spotify and YouTube Music
- **🎯 Smart Recognition**: 5-step priority system for accurate song matching
- **👤 User Isolation**: Complete data separation between users
- **📊 Sync Tracking**: Detailed logs and analytics of all sync operations
- **⚡ Real-time Processing**: Fast and efficient playlist synchronization
- **🔒 Secure Authentication**: OAuth 2.0 integration with both platforms

## 🚀 Live Demo

**Try it now**: [https://sync-tunes.onrender.com](https://sync-tunes.onrender.com)

## 🛠️ Technology Stack

### Backend
- **Flask 2.3.3** - Web framework
- **SQLAlchemy** - Database ORM
- **Flask-Login** - User authentication
- **bcrypt** - Password hashing

### APIs & Services
- **Spotify Web API** - Music streaming platform integration
- **YouTube Data API** - Video and playlist access
- **YouTube Music API** - Structured music data
- **Google Gemini AI** - Intelligent song title extraction
- **Groq AI** - Fallback AI processing

### Frontend
- **HTML5/CSS3** - Modern web interface
- **Bootstrap** - Responsive design
- **Font Awesome** - Icons and UI elements

### Deployment
- **Render.com** - Cloud hosting platform
- **PostgreSQL** - Production database
- **Gunicorn** - WSGI server

## 🧠 AI-Powered Song Extraction

Sync Tunes uses a sophisticated 5-step priority system for accurate song matching:

### 1. **Licensed Metadata** 🎼
- Extracts official song information from YouTube video metadata
- Highest accuracy for official music videos

### 2. **YouTube Music API** 🎵
- Searches YouTube Music's structured database
- Provides clean song titles and artist names

### 3. **Advanced Regex Cleaning** 🔧
- Removes common video suffixes (Official Video, Lyrics, HD, 4K)
- Handles various title formats and languages

### 4. **AI Extraction** 🤖
- **Primary**: Google Gemini AI for intelligent parsing
- **Fallback**: Groq AI when quota limits are reached
- Handles complex and messy YouTube titles

### 5. **Fuzzy Matching** 🎯
- Spotify search with 80% auto-accept threshold
- Intelligent matching with confidence scoring

## 📊 Confidence Scoring System

- **HIGH (≥95%)**: Auto-add to playlist
- **MEDIUM (≥90%)**: Auto-add with logging
- **LOW (≥50%)**: Require user confirmation
- **REJECT (<50%)**: Skip or manual selection

## 🗄️ Database Schema

### Core Models
- **User Management**: User accounts with secure authentication
- **Platform Integration**: Spotify and YouTube account connections
- **Music Data**: Songs, playlists, and cross-platform mappings
- **Sync Tracking**: Detailed logs of all synchronization operations
- **User Feedback**: Machine learning data for continuous improvement

### User Isolation
- Complete data separation between users
- User-specific song storage and playlist management
- Secure session management with automatic cleanup

## 🚀 Getting Started

### Prerequisites
- Python 3.9+
- Spotify Developer Account
- Google Cloud Console Account
- Gemini AI API Key
- Groq API Key

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/gorantlasadwik/sync-tunes.git
   cd sync-tunes
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables**
   ```bash
   cp env.example .env
   # Edit .env with your API keys
   ```

4. **Initialize the database**
   ```bash
   python init_db.py
   ```

5. **Run the application**
   ```bash
   python app.py
   ```

### Environment Variables

```bash
# Flask Configuration
FLASK_SECRET_KEY=your-secret-key

# AI APIs
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
```

## 📱 Usage

### 1. **Account Setup**
- Register for a new account or login
- Connect your Spotify account via OAuth
- Connect your YouTube account via OAuth

### 2. **Playlist Discovery**
- The app automatically fetches your playlists from both platforms
- View all your playlists in the dashboard

### 3. **Sync Operations**
- Select source and destination playlists
- Choose sync direction (Spotify ↔ YouTube)
- Review AI extraction results
- Confirm or correct song matches
- Monitor sync progress and results

### 4. **Track Results**
- View detailed sync logs
- Monitor success/failure rates
- Access sync history and analytics

## 🔧 API Setup Guides

### Spotify API Setup
1. Visit [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Add redirect URI: `https://your-domain.com/spotify_callback`
4. Copy Client ID and Client Secret

### YouTube API Setup
1. Visit [Google Cloud Console](https://console.cloud.google.com)
2. Enable YouTube Data API v3
3. Create OAuth 2.0 credentials
4. Add redirect URI: `https://your-domain.com/youtube_callback`

### AI API Setup
1. **Gemini AI**: Get API key from [Google AI Studio](https://makersuite.google.com)
2. **Groq AI**: Get API key from [Groq Console](https://console.groq.com)

## 📈 Performance Features

### Lazy Loading
- Original YouTube titles stored as-is during connection
- AI processing only during sync operations
- Prevents API overload and timeouts

### Caching Strategy
- User-specific quota tracking
- Efficient database query patterns
- Minimized redundant API calls

### Error Handling
- Graceful degradation when AI services fail
- Comprehensive error logging and reporting
- User-friendly error messages

## 🔒 Security Features

- **OAuth 2.0 Integration**: Secure authentication with both platforms
- **User Isolation**: Complete data separation between users
- **Session Management**: Secure session handling with automatic cleanup
- **Password Security**: bcrypt hashing with salt
- **API Security**: Secure token storage and validation

## 📊 Monitoring & Analytics

- **Sync Tracking**: Detailed logs of all operations
- **Performance Metrics**: Success rates and processing times
- **User Feedback**: Machine learning data collection
- **Error Monitoring**: Comprehensive error tracking and reporting

## 🛠️ Development

### Project Structure
```
sync-tunes/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── init_db.py            # Database initialization
├── Procfile              # Render deployment config
├── render.yaml           # Render service configuration
├── templates/            # HTML templates
│   ├── dashboard.html    # Main dashboard
│   ├── login.html        # Authentication
│   └── ...
└── instance/             # Database files
```

### Contributing
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 👨‍💻 Author

**Gorantla Sadwik**
- LinkedIn: [sadwik-gorantla-042362282](https://www.linkedin.com/in/sadwik-gorantla-042362282/)
- GitHub: [gorantlasadwik](https://github.com/gorantlasadwik)

## 🙏 Acknowledgments

- **Spotify** for their comprehensive Web API
- **Google** for YouTube Data API and Gemini AI
- **Groq** for fast AI inference capabilities
- **Render** for reliable cloud hosting
- **Flask** community for the excellent web framework

## 📞 Support

If you encounter any issues or have questions:

1. Check the [Issues](https://github.com/gorantlasadwik/sync-tunes/issues) page
2. Review the [Documentation](APPLICATION_DOCUMENTATION.md)
3. Contact: [LinkedIn](https://www.linkedin.com/in/sadwik-gorantla-042362282/)

---

**⭐ Star this repository if you find it helpful!**

*Built with ❤️ for music lovers who want seamless playlist synchronization across platforms.*
